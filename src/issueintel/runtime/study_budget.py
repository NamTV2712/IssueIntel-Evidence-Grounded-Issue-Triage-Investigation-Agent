"""Finite shared study allowances across native providers.

Call intents are committed before a caller may construct a provider. Immutable,
fsynced event files are authoritative; SQLite serializes admission and anchors
their exact bytes. This detects ordinary loss/tampering, not a coordinated
rollback of both journals and index, authorship, or a provider invoice.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import uuid


class StudyBudgetStop(OSError):
    """A study stop must escape controller RuntimeError/ValueError fallbacks."""


_HASH = re.compile(r'[a-f0-9]{64}')
_ID = re.compile(r'[a-f0-9]{32}')
_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}')
_TERMINAL = {'settled', 'failed_bound', 'missing_usage_bound', 'breach'}
_POLICY_KEYS = {'schema', 'study_id', 'config_sha256', 'providers', 'arms',
                'max_owned_attempts', 'max_owned_micro_usd'}
_PROVIDER_KEYS = {'provider', 'model', 'config_sha256', 'price_sha256', 'price_date', 'max_attempts'}
_MODELS = {'groq': 'openai/gpt-oss-120b', 'typesafe': 'jev-1.13.0'}
_CALL_KEYS = {'call_id', 'arm_id', 'stage', 'request_sha256', 'provider', 'model',
              'config_sha256', 'price_sha256', 'input_bound', 'output_bound', 'micro_usd_bound'}


def _encoded(value):
    try:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                           separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')
    except (ValueError, TypeError, UnicodeError):
        raise StudyBudgetStop('Study budget data is not canonical JSON') from None


def _digest(value):
    return sha256(_encoded(value)[:-1]).hexdigest()


def _hash(value):
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def _positive(value):
    return type(value) is int and value > 0


def _name(value):
    return isinstance(value, str) and _NAME.fullmatch(value) is not None


def _call_id(value):
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _utc(value):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
            raise ValueError()
        return parsed
    except (TypeError, ValueError, AttributeError):
        raise StudyBudgetStop('Invalid UTC study timestamp') from None


def _validate_policy(policy):
    if (not isinstance(policy, dict) or set(policy) != _POLICY_KEYS
            or policy['schema'] != 'study_budget_policy_v1'
            or not _hash(policy['study_id']) or not _hash(policy['config_sha256'])
            or not _positive(policy['max_owned_attempts']) or policy['max_owned_attempts'] > 10000
            or not _positive(policy['max_owned_micro_usd'])
            or not isinstance(policy['providers'], list) or not 1 <= len(policy['providers']) <= 2
            or not isinstance(policy['arms'], list) or not 1 <= len(policy['arms']) <= 100):
        raise StudyBudgetStop('Invalid finite study budget policy')
    providers, arms = {}, {}
    for row in policy['providers']:
        try:
            valid_date = date.fromisoformat(row['price_date']).isoformat() == row['price_date']
        except (KeyError, TypeError, ValueError):
            valid_date = False
        if (not isinstance(row, dict) or set(row) != _PROVIDER_KEYS
                or not isinstance(row['provider'], str) or row['provider'] not in _MODELS or row['provider'] in providers
                or row['model'] != _MODELS[row['provider']] or not _hash(row['config_sha256'])
                or not _hash(row['price_sha256']) or not valid_date
                or not _positive(row['max_attempts'])):
            raise StudyBudgetStop('Invalid or duplicate study provider policy')
        providers[row['provider']] = deepcopy(row)
    for row in policy['arms']:
        if (not isinstance(row, dict) or set(row) != {'arm_id', 'max_micro_usd', 'max_attempts'}
                or not _name(row['arm_id']) or row['arm_id'] in arms
                or not _positive(row['max_micro_usd']) or not _positive(row['max_attempts'])):
            raise StudyBudgetStop('Invalid or duplicate study arm allowance')
        arms[row['arm_id']] = deepcopy(row)
    return providers, arms


def _safe(path):
    """Reject links/reparse aliases before traversing an owned ledger path."""
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise StudyBudgetStop('Study budget paths cannot traverse links or reparse points')
        if part == path and stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise StudyBudgetStop('Study budget file has an aliased hardlink')
    return path


def _read(path, maximum=262144):
    path = _safe(path)
    if not path.is_file() or path.stat().st_size > maximum:
        raise StudyBudgetStop('Missing or oversized study budget journal')
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise StudyBudgetStop('Invalid study budget journal JSON') from None
    if _encoded(value) != raw:
        raise StudyBudgetStop('Study budget journal bytes changed')
    return value


def _write_new(path, value):
    raw = _encoded(value)
    with _safe(path).open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


class StudyBudgetLedger:
    """Shared logical arm caps and deduplicated actual owned reservations.

No API here calls providers or grants spending authority. The owner must verify
dated prices, calculate the native bound, and reserve before constructing its
provider. Native provider limits remain additional admission checks.
    """

    def __init__(self, directory, policy, *, clock=None):
        self.providers, self.arms = _validate_policy(policy)
        self.policy = deepcopy(policy)
        self.policy_sha256 = _digest(policy)
        self.directory = _safe(directory)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        if not (self.directory / 'policy.json').exists():
            today = self.clock().astimezone(timezone.utc).date()
            if any(not 0 <= (today - date.fromisoformat(row['price_date'])).days <= 30
                   for row in self.providers.values()):
                raise StudyBudgetStop('A new study requires fresh dated provider prices')
        self.directory.mkdir(parents=True, exist_ok=True)
        self.events_directory = _safe(self.directory / 'events')
        self.events_directory.mkdir(exist_ok=True)
        self.path = _safe(self.directory / 'index.sqlite')
        with self._connect() as db:
            names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view','trigger')")}
            if names - {'meta', 'indexed_events'}:
                raise StudyBudgetStop('Unexpected study index schema')
            db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS indexed_events (seq INTEGER PRIMARY KEY, sha256 TEXT NOT NULL, event_json TEXT NOT NULL)')
            metadata = dict(db.execute('SELECT key,value FROM meta'))
            expected = {'schema': 'study_budget_index_v1', 'policy_sha256': self.policy_sha256}
            if metadata and metadata != expected:
                raise StudyBudgetStop('Study budget index policy changed')
            if not metadata:
                if db.execute('SELECT 1 FROM indexed_events LIMIT 1').fetchone():
                    raise StudyBudgetStop('Study budget index metadata is missing')
                db.executemany('INSERT INTO meta VALUES (?,?)', expected.items())
            path = self.directory / 'policy.json'
            if path.exists():
                if _read(path) != self.policy:
                    raise StudyBudgetStop('Frozen study budget policy changed')
            else:
                if any(self.events_directory.iterdir()) or db.execute('SELECT 1 FROM indexed_events LIMIT 1').fetchone():
                    raise StudyBudgetStop('Study budget policy was removed')
                _write_new(path, self.policy)
            self._load(db)

    @contextmanager
    def _connect(self):
        db = None
        try:
            _safe(self.directory)
            _safe(self.path)
            db = sqlite3.connect(self.path, timeout=30)
            db.execute('PRAGMA busy_timeout=30000')
            db.execute('PRAGMA journal_mode=DELETE')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise StudyBudgetStop('Study budget index integrity failure')
            with db:
                yield db
        except sqlite3.Error:
            raise StudyBudgetStop('Study budget index unavailable') from None
        finally:
            if db is not None:
                db.close()

    def _inventory(self):
        for path in self.directory.iterdir():
            _safe(path)
            if path.name == 'events':
                if not path.is_dir():
                    raise StudyBudgetStop('Study event inventory is not a directory')
            elif path.name not in {'policy.json', 'index.sqlite', 'index.sqlite-journal'} or not path.is_file():
                raise StudyBudgetStop('Unexpected study budget inventory')
        if _read(self.directory / 'policy.json') != self.policy:
            raise StudyBudgetStop('Frozen study budget policy changed')
        paths = sorted(self.events_directory.iterdir())
        maximum = self.policy['max_owned_attempts'] * (len(self.arms) * 2 + 8) + 10
        if len(paths) > maximum:
            raise StudyBudgetStop('Study budget event inventory exceeds its bound')
        return paths

    def _load(self, db):
        names = {(row[0], row[1]) for row in db.execute(
            "SELECT name,type FROM sqlite_master WHERE type IN ('table','view','trigger')")}
        if names != {('meta', 'table'), ('indexed_events', 'table')}:
            raise StudyBudgetStop('Unexpected study index schema')
        providers, arms = _validate_policy(self.policy)
        if (_digest(self.policy) != self.policy_sha256
                or providers != self.providers or arms != self.arms):
            raise StudyBudgetStop('In-memory study policy differs from its frozen identity')
        expected = {'schema': 'study_budget_index_v1', 'policy_sha256': self.policy_sha256}
        if dict(db.execute('SELECT key,value FROM meta')) != expected:
            raise StudyBudgetStop('Study budget index policy changed')
        indexed = db.execute('SELECT seq,sha256,event_json FROM indexed_events ORDER BY seq').fetchall()
        paths = self._inventory()
        if len(indexed) > len(paths):
            raise StudyBudgetStop('Indexed study budget events were removed')
        state = {'calls': {}, 'stages': {}, 'signatures': {}, 'reservation_owners': {}, 'locked': False,
                 'events': [], 'stop_reasons': []}
        previous = None
        for number, path in enumerate(paths, 1):
            event = _read(path)
            if (not isinstance(event, dict)
                    or set(event) != {'schema', 'seq', 'previous_sha256', 'policy_sha256', 'recorded_at', 'kind', 'payload', 'sha256'}
                    or event['schema'] != 'study_budget_event_v1' or event['seq'] != number
                    or type(event['seq']) is not int or event['previous_sha256'] != previous
                    or event['policy_sha256'] != self.policy_sha256
                    or event['sha256'] != _digest({k: v for k, v in event.items() if k != 'sha256'})
                    or path.name != f'{number:08d}-{event["sha256"]}.json'):
                raise StudyBudgetStop('Study budget event chain changed')
            _utc(event['recorded_at'])
            if number <= len(indexed) and indexed[number - 1] != (number, event['sha256'], _encoded(event).decode()):
                raise StudyBudgetStop('Study budget event differs from its indexed bytes')
            self._apply(state, event)
            state['events'].append(event)
            if number > len(indexed):
                # A durable intent may precede SQLite commit after a crash. It
                # remains an owned reservation, never permission to retry.
                db.execute('INSERT INTO indexed_events VALUES (?,?,?)',
                           (number, event['sha256'], _encoded(event).decode()))
            previous = event['sha256']
        return state

    def _after_event_persist(self, event):
        """Crash-boundary seam; production has no work between fsync and index."""

    def _append(self, db, state, kind, payload):
        maximum = self.policy['max_owned_attempts'] * (len(self.arms) * 2 + 8) + 10
        if len(state['events']) >= maximum:
            raise StudyBudgetStop('Study budget event inventory exceeds its bound')
        event = {'schema': 'study_budget_event_v1', 'seq': len(state['events']) + 1,
                 'previous_sha256': state['events'][-1]['sha256'] if state['events'] else None,
                 'policy_sha256': self.policy_sha256, 'recorded_at': self.clock().astimezone(timezone.utc).isoformat(),
                 'kind': kind, 'payload': deepcopy(payload)}
        event['sha256'] = _digest(event)
        # Validate before emitting, using the same transition rules as replay.
        projected = deepcopy(state)
        self._apply(projected, event)
        path = self.events_directory / f'{event["seq"]:08d}-{event["sha256"]}.json'
        _write_new(path, event)
        self._after_event_persist(deepcopy(event))
        db.execute('INSERT INTO indexed_events VALUES (?,?,?)',
                   (event['seq'], event['sha256'], _encoded(event).decode()))
        return projected

    @staticmethod
    def _charged(call):
        receipt = call['receipt']
        return receipt['micro_usd_charged_or_reserved'] if receipt is not None else call['micro_usd_bound']

    def _totals(self, state):
        calls = list(state['calls'].values())
        arms = {key: {'attempts': 0, 'micro_usd_charged_or_reserved': 0} for key in self.arms}
        providers = {key: {'attempts': 0, 'micro_usd_charged_or_reserved': 0} for key in self.providers}
        for call in calls:
            cost = self._charged(call)
            providers[call['provider']]['attempts'] += 1
            providers[call['provider']]['micro_usd_charged_or_reserved'] += cost
            for arm_id in {row['arm_id'] for row in call['references']}:
                arms[arm_id]['attempts'] += 1
                arms[arm_id]['micro_usd_charged_or_reserved'] += cost
        return {'owned_attempts': len(calls),
                'owned_micro_usd_charged_or_reserved': sum(self._charged(c) for c in calls),
                'by_provider': providers, 'arms': arms}

    def _allow(self, state):
        if state['locked']:
            raise StudyBudgetStop('Study budget has a durable integrity or usage stop')

    def _limits(self, state):
        totals = self._totals(state)
        if (totals['owned_attempts'] > self.policy['max_owned_attempts']
                or totals['owned_micro_usd_charged_or_reserved'] > self.policy['max_owned_micro_usd']
                or any(totals['by_provider'][p]['attempts'] > row['max_attempts'] for p, row in self.providers.items())
                or any(totals['arms'][a]['attempts'] > row['max_attempts']
                       or totals['arms'][a]['micro_usd_charged_or_reserved'] > row['max_micro_usd']
                       for a, row in self.arms.items())):
            raise StudyBudgetStop('Finite shared study allowance exhausted')

    def _validate_call(self, value):
        if (not isinstance(value, dict) or set(value) != _CALL_KEYS
                or not isinstance(value['call_id'], str) or _ID.fullmatch(value['call_id']) is None
                or not isinstance(value['arm_id'], str) or value['arm_id'] not in self.arms or not _name(value['stage'])
                or not _hash(value['request_sha256']) or not isinstance(value['provider'], str) or value['provider'] not in self.providers
                or not _positive(value['input_bound']) or type(value['output_bound']) is not int
                or value['output_bound'] < 0 or not _positive(value['micro_usd_bound'])):
            raise StudyBudgetStop('Invalid study call reservation')
        native = self.providers[value['provider']]
        if any(value[key] != native[key] for key in ('model', 'config_sha256', 'price_sha256')):
            raise StudyBudgetStop('Study call differs from its frozen provider or price')
        if value['provider'] == 'groq' and (value['output_bound'] == 0 or value['input_bound'] + value['output_bound'] > 131072):
            raise StudyBudgetStop('Groq study call exceeds the pinned context')
        if value['provider'] == 'typesafe':
            from .typesafe import INPUT_BOUND, MICRO_BOUND
            if (value['input_bound'] != INPUT_BOUND or value['output_bound'] != 0
                    or value['micro_usd_bound'] != MICRO_BOUND):
                raise StudyBudgetStop('Typesafe study call differs from its native bound')

    @staticmethod
    def _signature(value):
        return tuple(value[key] for key in ('provider', 'model', 'config_sha256', 'price_sha256', 'request_sha256'))

    def _validate_receipt(self, call, receipt):
        provider = self.providers[call['provider']]
        if not isinstance(receipt, dict):
            raise StudyBudgetStop('Native receipt must be an object')
        if call['provider'] == 'typesafe':
            from .typesafe import validate_receipt, TypesafeError
            try:
                validate_receipt(receipt, request_sha256=call['request_sha256'])
            except TypesafeError:
                raise StudyBudgetStop('Native Typesafe receipt binding is invalid') from None
        else:
            required = {'reservation_id', 'created_at', 'model', 'input_bound', 'output_bound',
                        'tokens_charged_or_reserved', 'micro_usd_charged_or_reserved', 'status',
                        'price_date', 'observed_input_tokens', 'observed_output_tokens'}
            if (set(receipt) != required or type(receipt.get('output_bound')) is not int
                    or receipt.get('output_bound') != call['output_bound']):
                raise StudyBudgetStop('Native Groq receipt shape or output bound changed')
        if (receipt.get('model') != call['model'] or type(receipt.get('input_bound')) is not int
                or receipt.get('input_bound') != call['input_bound']
                or receipt.get('price_date') != provider['price_date']
                or not isinstance(receipt.get('reservation_id'), str) or _ID.fullmatch(receipt['reservation_id']) is None
                or not isinstance(receipt.get('status'), str) or receipt['status'] not in _TERMINAL | {'reserved'}
                or type(receipt.get('micro_usd_charged_or_reserved')) is not int
                or receipt['micro_usd_charged_or_reserved'] < 0):
            raise StudyBudgetStop('Native receipt differs from its study reservation')
        _utc(receipt.get('created_at'))
        charge = receipt['micro_usd_charged_or_reserved']
        status = receipt['status']
        if status in {'reserved', 'failed_bound', 'missing_usage_bound'} and charge < call['micro_usd_bound']:
            raise StudyBudgetStop('Unknown native usage must retain the original bound')
        if call['provider'] == 'groq':
            incoming, outgoing = receipt['observed_input_tokens'], receipt['observed_output_tokens']
            if type(receipt['tokens_charged_or_reserved']) is not int or receipt['tokens_charged_or_reserved'] < 0:
                raise StudyBudgetStop('Invalid native token accounting')
            if status in {'reserved', 'failed_bound', 'missing_usage_bound'} and (
                    incoming is not None or outgoing is not None
                    or receipt['tokens_charged_or_reserved'] != call['input_bound'] + call['output_bound']):
                raise StudyBudgetStop('Unknown native token usage lost its bound')
            if status == 'settled':
                # Native low-effort product calls have no call_context row.
                # Their reconciled total/USD are known while the optional
                # per-direction observations legitimately remain both None.
                if (not _positive(receipt['tokens_charged_or_reserved'])
                        or (incoming is None) != (outgoing is None)
                        or incoming is not None and (
                            not _positive(incoming) or type(outgoing) is not int or outgoing < 0
                            or receipt['tokens_charged_or_reserved'] != incoming + outgoing)):
                    raise StudyBudgetStop('Settled native token usage is inconsistent')
            breach = (status == 'breach' or charge > call['micro_usd_bound']
                      or receipt['tokens_charged_or_reserved'] > call['input_bound'] + call['output_bound']
                      or type(incoming) is int and incoming > call['input_bound']
                      or type(outgoing) is int and outgoing > call['output_bound'])
        else:
            breach = status == 'breach' or charge > call['micro_usd_bound']
        return breach

    def _apply(self, state, event):
        kind, value = event['kind'], event['payload']
        if kind == 'reserve':
            self._allow(state)
            self._validate_call(value)
            price_date = date.fromisoformat(self.providers[value['provider']]['price_date'])
            if not 0 <= (_utc(event['recorded_at']).date() - price_date).days <= 30:
                raise StudyBudgetStop('Recorded call used stale provider prices')
            signature = self._signature(value)
            key = (value['arm_id'], value['stage'])
            if value['call_id'] in state['calls'] or signature in state['signatures'] or key in state['stages']:
                raise StudyBudgetStop('A started study call cannot be reserved or retried again')
            state['calls'][value['call_id']] = {**deepcopy(value), 'receipt': None,
                'outcome': 'pending', 'outcome_sha256': None, 'reason': None,
                'references': [{key: value[key] for key in ('arm_id', 'stage', 'request_sha256')}]}
            state['signatures'][signature] = value['call_id']
            state['stages'][key] = value['call_id']
            self._limits(state)
        elif kind == 'receipt':
            if (not isinstance(value, dict) or set(value) != {'call_id', 'receipt'}
                    or not _call_id(value['call_id']) or value['call_id'] not in state['calls']):
                raise StudyBudgetStop('Native receipt lacks a study owner')
            call, receipt = state['calls'][value['call_id']], value['receipt']
            breach = self._validate_receipt(call, receipt)
            rid = receipt['reservation_id']
            if rid in state['reservation_owners'] and state['reservation_owners'][rid] != value['call_id']:
                raise StudyBudgetStop('A native reservation has multiple study owners')
            old = call['receipt']
            if old is not None and (old['reservation_id'] != rid or old['status'] in _TERMINAL and old != receipt):
                raise StudyBudgetStop('An owned or terminal native receipt cannot change')
            call['receipt'] = deepcopy(receipt)
            state['reservation_owners'][rid] = value['call_id']
            if breach:
                state['locked'] = True
                state['stop_reasons'].append('native_usage_breach')
        elif kind == 'finish':
            if (not isinstance(value, dict) or set(value) != {'call_id', 'outcome', 'outcome_sha256', 'reason'}
                    or not _call_id(value['call_id']) or value['call_id'] not in state['calls']
                    or not isinstance(value['outcome'], str) or value['outcome'] not in {'completed', 'failed', 'unknown'}
                    or value['reason'] is not None and not _name(value['reason'])):
                raise StudyBudgetStop('Invalid study call outcome')
            call = state['calls'][value['call_id']]
            if call['outcome'] != 'pending':
                raise StudyBudgetStop('A closed study operation cannot change')
            if value['outcome'] == 'completed':
                if (not _hash(value['outcome_sha256']) or call['receipt'] is None
                        or call['receipt']['status'] not in {'settled', 'missing_usage_bound'} or state['locked']):
                    raise StudyBudgetStop('Completed shared work requires a returned closed receipt and payload hash')
            elif value['outcome_sha256'] is not None:
                raise StudyBudgetStop('Failed or unknown work cannot claim a reusable payload')
            call.update({key: deepcopy(value[key]) for key in ('outcome', 'outcome_sha256', 'reason')})
        elif kind == 'reference':
            self._allow(state)
            if (not isinstance(value, dict) or set(value) != {'call_id', 'arm_id', 'stage', 'request_sha256'}
                    or not _call_id(value['call_id']) or value['call_id'] not in state['calls']
                    or not isinstance(value['arm_id'], str) or value['arm_id'] not in self.arms
                    or not _name(value['stage']) or not _hash(value['request_sha256'])):
                raise StudyBudgetStop('Invalid shared study reference')
            call = state['calls'][value['call_id']]
            key = (value['arm_id'], value['stage'])
            if (call['outcome'] == 'pending' or value['request_sha256'] != call['request_sha256']
                    or key in state['stages']):
                raise StudyBudgetStop('Shared accounting is unfinished or differs from its exact binding')
            call['references'].append({key: value[key] for key in ('arm_id', 'stage', 'request_sha256')})
            state['stages'][key] = value['call_id']
            self._limits(state)
        elif kind == 'stop':
            if (not isinstance(value, dict) or set(value) != {'reason', 'evidence_sha256'}
                    or not _name(value['reason']) or not _hash(value['evidence_sha256'])):
                raise StudyBudgetStop('Invalid study integrity stop')
            state['locked'] = True
            state['stop_reasons'].append(value['reason'])
        else:
            raise StudyBudgetStop('Unexpected study budget event type')

    def reserve(self, arm_id, stage, request_sha256, provider, model, config_sha256,
                price_sha256, input_bound, output_bound, micro_usd_bound):
        call = dict(call_id=uuid.uuid4().hex, arm_id=arm_id, stage=stage,
                    request_sha256=request_sha256, provider=provider, model=model,
                    config_sha256=config_sha256, price_sha256=price_sha256,
                    input_bound=input_bound, output_bound=output_bound, micro_usd_bound=micro_usd_bound)
        self._validate_call(call)
        today = self.clock().astimezone(timezone.utc).date()
        if not 0 <= (today - date.fromisoformat(self.providers[provider]['price_date'])).days <= 30:
            raise StudyBudgetStop('Study dated provider price needs reverification')
        with self._connect() as db:
            state = self._load(db)
            self._append(db, state, 'reserve', call)
        return call['call_id']

    def publish_receipt(self, call_id, native_receipt):
        if not _call_id(call_id):
            raise StudyBudgetStop('Invalid study call identity')
        stopped = False
        with self._connect() as db:
            state = self._load(db)
            if call_id not in state['calls']:
                raise StudyBudgetStop('Native receipt lacks a study owner')
            try:
                breach = self._validate_receipt(state['calls'][call_id], native_receipt)
                if state['calls'][call_id]['receipt'] == native_receipt:
                    return
                self._apply(deepcopy(state), {'kind': 'receipt', 'payload': {'call_id': call_id, 'receipt': native_receipt}})
            except StudyBudgetStop:
                # A mismatch must not erase the prior conservative reservation.
                self._append(db, state, 'stop', {'reason': 'native_receipt_integrity_failure',
                    'evidence_sha256': _digest({'call_id': call_id, 'receipt': native_receipt})})
                stopped = True
            else:
                # Storage/crash exceptions from append must escape unchanged;
                # a durable event beyond the SQLite index is replayed on reopen.
                projected = self._append(db, state, 'receipt', {'call_id': call_id, 'receipt': native_receipt})
                stopped = breach or projected['locked']
        if stopped:
            raise StudyBudgetStop('Native receipt retained; study stopped for accounting review')

    def finish(self, call_id, outcome='completed', outcome_sha256=None, *, reason=None):
        if not _call_id(call_id):
            raise StudyBudgetStop('Invalid study call identity')
        value = {'call_id': call_id, 'outcome': outcome, 'outcome_sha256': outcome_sha256, 'reason': reason}
        with self._connect() as db:
            state = self._load(db)
            call = state['calls'].get(call_id)
            if call is not None and all(call.get(key) == value[key] for key in ('outcome', 'outcome_sha256', 'reason')):
                return
            self._append(db, state, 'finish', value)

    def close_unknown(self, call_id, reason):
        return self.finish(call_id, 'unknown', reason=reason)

    def reference(self, call_id, arm_id, stage, request_sha256):
        """Attribute a terminal call's cost, including failed repair attempts.

This is accounting only: it never authorizes reusing a payload, executing a
request, or treating a failed/unknown call as successful. A stage-cache owner
must independently validate the complete successful payload and attribute all
its calls, including failures preceding a successful schema repair.
        """
        if not _call_id(call_id):
            raise StudyBudgetStop('Invalid study call identity')
        value = {'call_id': call_id, 'arm_id': arm_id, 'stage': stage, 'request_sha256': request_sha256}
        with self._connect() as db:
            state = self._load(db)
            self._allow(state)
            call = state['calls'].get(call_id)
            if (call is not None and call['outcome'] != 'pending' and _name(stage)
                    and _hash(request_sha256) and request_sha256 == call['request_sha256']
                    and {key: value[key] for key in ('arm_id', 'stage', 'request_sha256')} in call['references']):
                return call_id
            self._append(db, state, 'reference', value)
        return call_id

    def inspect(self):
        with self._connect() as db:
            state = self._load(db)
            return {'schema': 'study_budget_inspection_v1', 'policy_sha256': self.policy_sha256,
                    'locked': state['locked'], 'stop_reasons': list(state['stop_reasons']),
                    'event_count': len(state['events']), **self._totals(state),
                    'calls': deepcopy(list(state['calls'].values())),
                    'is_provider_invoice': False, 'automatic_retry': False,
                    'integrity_scope': 'event/index byte consistency; not authorship or coordinated rollback protection'}
