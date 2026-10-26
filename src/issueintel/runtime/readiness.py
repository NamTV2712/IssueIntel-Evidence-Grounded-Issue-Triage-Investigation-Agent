"""Bounded local configuration inspection, without initialization or live calls.

Configured is not authenticated, and a ready provider is not a budget reservation.
All diagnostics are stable codes: raw configuration, exceptions and secrets never
enter the report. Existing SQLite state is opened read-only, including its WAL.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import time


_REPARSE = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)
_MAX_CONFIG = 256_000
_MAX_CONNECTIONS = 50
_MAX_RECORD = 65_536
_REPOSITORY = re.compile(r'[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}')

PREREQUISITE_MESSAGES = {
    'python_too_old': 'Python 3.11 or newer is required.',
    'source_incomplete': 'Run this file from a complete IssueIntel source checkout.',
    'git_missing': 'Git is required. Install Git and restart the terminal.',
    'git_unavailable': 'Git could not start. Check the Git installation.',
    'fts5_unavailable': 'This Python runtime needs SQLite with FTS5 support.',
}


def _root(root):
    # Do not resolve links before checking their ancestors.
    return Path(os.path.abspath(os.fspath(root)))


def _path_status(root, path, *, directory=False, max_bytes=None):
    """Inspect every existing ancestor before following any path component."""
    root = _root(root)
    path = Path(path)
    path = path if path.is_absolute() else root / path
    if '..' in path.parts:
        return 'unsafe'
    if not path.is_relative_to(root):
        return 'unsafe'
    cursor = Path(path.anchor)
    try:
        for part in path.parts[1:]:
            cursor = cursor / part
            value = cursor.lstat()
            if stat.S_ISLNK(value.st_mode) or getattr(value, 'st_file_attributes', 0) & _REPARSE:
                return 'unsafe'
            if cursor != path and not stat.S_ISDIR(value.st_mode):
                return 'unavailable'
        value = path.lstat()
        if directory and not stat.S_ISDIR(value.st_mode):
            return 'unavailable'
        if not directory and not stat.S_ISREG(value.st_mode):
            return 'unavailable'
        if max_bytes is not None and value.st_size > max_bytes:
            return 'unavailable'
        return 'present'
    except FileNotFoundError:
        return 'missing'
    except OSError:
        return 'unavailable'


def prerequisites(root):
    """Shared stdlib-only prerequisite check, independent of optional extras."""
    root = _root(root)
    problems = []
    if sys.version_info < (3, 11):
        problems.append('python_too_old')
    source_complete = all(_path_status(root, path) == 'present' for path in
                          ('src/issueintel/interfaces/web.py', 'configs/runtime.toml'))
    if not source_complete:
        problems.append('source_incomplete')
    git = shutil.which('git')
    git_available = git is not None
    if not git:
        problems.append('git_missing')
    else:
        try:
            result = subprocess.run([git, '--version'], capture_output=True, timeout=2)
            if result.returncode:
                git_available = False
                problems.append('git_unavailable')
        except (OSError, subprocess.TimeoutExpired):
            git_available = False
            problems.append('git_unavailable')
    fts5_available = True
    try:
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE VIRTUAL TABLE check_fts USING fts5(text)')
    except sqlite3.DatabaseError:
        fts5_available = False
        problems.append('fts5_unavailable')
    return {'ready': not problems, 'python': '.'.join(map(str, sys.version_info[:3])),
            'git_available': git_available, 'fts5_available': fts5_available,
            'source_complete': source_complete, 'problems': problems}


def _configuration(root, name):
    import tomllib
    path = root / 'configs' / name
    status = _path_status(root, path, max_bytes=_MAX_CONFIG)
    if status != 'present':
        raise ValueError('unsafe_configuration_path' if status == 'unsafe' else 'invalid_configuration')
    row = tomllib.loads(path.read_text(encoding='utf-8'))
    for field in ('price_file', 'ledger'):
        raw = row.get(field)
        if not isinstance(raw, str) or not raw or '\x00' in raw:
            raise ValueError('invalid_configuration')
        target = Path(raw)
        target = target if target.is_absolute() else root / target
        status = _path_status(root, target, max_bytes=_MAX_CONFIG if field == 'price_file' else None)
        if status == 'unsafe':
            raise ValueError('unsafe_configuration_path')
        if field == 'ledger' and status == 'unavailable':
            raise ValueError('invalid_configuration')
    if name == 'runtime.toml' and (row.get('tokens_unlimited_authorized') or row.get('usd_unlimited_authorized')):
        status = _path_status(root, root / 'configs/author_decisions.json', max_bytes=_MAX_CONFIG)
        if status != 'present':
            raise ValueError('unsafe_configuration_path' if status == 'unsafe' else 'invalid_configuration')
    return row


def _groq(root):
    result = {'ready': False, 'key_configured': bool(os.environ.get('GROQ_API_KEY', '').strip()),
              'model': 'openai/gpt-oss-120b', 'reason': 'invalid_configuration',
              'price_date': None, 'budget_checked': False, 'live_verified': False}
    try:
        _configuration(root, 'runtime.toml')
        from .config import RuntimeConfig
        config = RuntimeConfig.load(root)
        status = _path_status(root, config.price_file, max_bytes=_MAX_CONFIG)
        if status != 'present':
            result['reason'] = 'unsafe_configuration_path' if status == 'unsafe' else 'invalid_or_stale_prices'
            return result
        try:
            price = config.price()
        except Exception:
            result['reason'] = 'invalid_or_stale_prices'
            return result
        if (price.context_tokens <= 0 or price.max_output_tokens <= 0
                or config.max_output_tokens >= price.context_tokens
                or config.max_output_tokens > price.max_output_tokens):
            result['reason'] = 'invalid_per_call_bounds'
            return result
        result['price_date'] = price.checked_at
        result['reason'] = 'ready' if result['key_configured'] else 'missing_api_key'
        result['ready'] = result['reason'] == 'ready'
    except Exception as error:
        if isinstance(error, ValueError) and str(error) == 'unsafe_configuration_path':
            result['reason'] = 'unsafe_configuration_path'
    return result


def _typesafe(root):
    result = {'ready': False, 'key_configured': bool(os.environ.get('TYPESAFE_API_KEY', '').strip()),
              'enabled': False, 'model': 'jev-1.13.0', 'reason': 'invalid_configuration',
              'price_date': None, 'budget_checked': False, 'live_verified': False}
    try:
        _configuration(root, 'typesafe.toml')
        from .typesafe import TypesafeConfig, _price
        config = TypesafeConfig.load(root)
        result['enabled'] = config.enabled
        status = _path_status(root, config.price_file, max_bytes=_MAX_CONFIG)
        if status == 'unsafe':
            result['reason'] = 'unsafe_configuration_path'
            return result
        if status == 'present':
            try:
                result['price_date'] = _price(config.price_file)
            except Exception:
                pass
        if not config.enabled:
            result['reason'] = 'disabled'
        elif result['price_date'] is None:
            result['reason'] = 'invalid_or_stale_prices'
        else:
            status = config.status()
            result['reason'] = status['reason']
            result['ready'] = status['ready']
    except Exception as error:
        if isinstance(error, ValueError) and str(error) == 'unsafe_configuration_path':
            result['reason'] = 'unsafe_configuration_path'
        elif getattr(error, 'reason', None) == 'missing_spending_limits':
            result['reason'] = 'missing_spending_limits'
    return result


def _app_key_status(root, raw):
    """Local metadata only; an explicitly configured PEM may be outside root."""
    try:
        path = Path(raw)
        path = _root(path if path.is_absolute() else root / path)
        # Do not turn a local status refresh into a UNC/network-file lookup.
        if path.anchor.startswith(('\\\\', '//')):
            return 'unsafe'
        cursor = Path(path.anchor)
        for part in path.parts[1:]:
            cursor = cursor / part
            value = cursor.lstat()
            if (stat.S_ISLNK(value.st_mode) or getattr(value, 'st_file_attributes', 0) & _REPARSE
                    or stat.S_ISREG(value.st_mode) and value.st_nlink != 1):
                return 'unsafe'
            if cursor != path and not stat.S_ISDIR(value.st_mode):
                return 'unavailable'
        value = path.lstat()
        return ('present' if stat.S_ISREG(value.st_mode) and 0 < value.st_size <= 65_536
                else 'unavailable')
    except FileNotFoundError:
        return 'missing'
    except (OSError, TypeError, ValueError):
        return 'unavailable'


def _app_dependencies_present():
    # Top-level find_spec inspects availability without importing either package
    # or cryptography backends. The stdlib-only launcher does not call this.
    from importlib.util import find_spec
    try:
        return all(find_spec(name) is not None for name in ('jwt', 'cryptography'))
    except Exception:
        return False


def _github_app(root):
    """Inspect known local blockers, never load PEM bytes, sign or mint a token."""
    names = ('GITHUB_APP_ID', 'GITHUB_INSTALLATION_ID', 'GITHUB_APP_CLIENT_ID',
             'GITHUB_APP_PRIVATE_KEY_PATH', 'GITHUB_WEBHOOK_SECRET')
    raw = {name: os.environ.get(name, '') for name in names}
    if any(not isinstance(value, str) or len(value) > (32768 if name == 'GITHUB_APP_PRIVATE_KEY_PATH' else 4096)
           or any(ord(char) < 32 for char in value) for name, value in raw.items()):
        return 'blocked', 'app_configuration_invalid'
    values = {name: value.strip() for name, value in raw.items()}
    if not any(values.values()):
        return 'blocked', 'app_not_configured'
    for name in ('GITHUB_APP_ID', 'GITHUB_INSTALLATION_ID'):
        value = values[name]
        if value and (not re.fullmatch(r'[1-9][0-9]{0,18}', value) or int(value) > (1 << 63) - 1):
            return 'blocked', 'app_configuration_invalid'
    client = values['GITHUB_APP_CLIENT_ID']
    if client and not re.fullmatch(r'[A-Za-z0-9_.-]{1,200}', client):
        return 'blocked', 'app_configuration_invalid'
    if not values['GITHUB_APP_ID']:
        return 'blocked', 'app_id_missing'
    if not values['GITHUB_APP_PRIVATE_KEY_PATH']:
        return 'blocked', 'app_private_key_missing'
    key = _app_key_status(root, values['GITHUB_APP_PRIVATE_KEY_PATH'])
    if key != 'present':
        return 'blocked', {'missing': 'app_private_key_missing', 'unsafe': 'app_private_key_unsafe',
                           'unavailable': 'app_private_key_unavailable'}[key]
    if not values['GITHUB_WEBHOOK_SECRET']:
        return 'blocked', 'app_webhook_secret_missing'
    if not _app_dependencies_present():
        return 'blocked', 'app_signing_dependencies_missing'
    # Metadata presence cannot prove valid PEM contents, signatures, credentials,
    # installation grants or HTTPS delivery. Actual operations still authorize.
    return 'unknown', 'app_https_permissions_not_verified'


def _readonly(root, path, deadline):
    """No CREATE, initialization, immutable mode or new database on a read."""
    statuses = []
    for candidate in (path, Path(str(path) + '-wal'), Path(str(path) + '-shm')):
        status = _path_status(root, candidate)
        statuses.append(status)
        if candidate == path and status != 'present':
            raise ValueError('index_not_initialized' if status == 'missing' else 'unsafe_local_path')
        if status not in {'present', 'missing'}:
            raise ValueError('unsafe_local_path')
    # SQLite mode=ro may otherwise create missing WAL/SHM companions. Never use
    # immutable mode to avoid that: it can silently hide committed live WAL rows.
    with path.open('rb') as handle:
        header = handle.read(20)
    if len(header) != 20 or header[:16] != b'SQLite format 3\x00':
        raise ValueError('invalid_local_state')
    if header[18:20] != b'\x01\x01' and statuses[1:] != ['present', 'present']:
        raise ValueError('local_index_unavailable')
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=0.1)
    try:
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, _MAX_RECORD * 4)
        db.execute('PRAGMA query_only=ON')
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
    except Exception:
        db.close()
        raise
    return db


def _json_record(raw):
    if not isinstance(raw, str) or len(raw.encode('utf-8')) > _MAX_RECORD:
        raise ValueError('invalid_local_state')
    return json.loads(raw)


def _index_counts(root, repository, deadline):
    from .io import digest
    path = root / 'data/repository_index' / digest(repository)[:24] / 'index.sqlite'
    db = _readonly(root, path, deadline)
    try:
        row = db.execute("SELECT value FROM meta WHERE key='repository'").fetchone()
        if row is None or _json_record(row[0]) != repository:
            raise ValueError('invalid_local_state')
        row = db.execute("SELECT value FROM meta WHERE key='active_run'").fetchone()
        active = _json_record(row[0]) if row else None
        if active is not None and (not isinstance(active, str) or len(active) > 128):
            raise ValueError('invalid_local_state')
        pending = db.execute('SELECT COUNT(*) FROM tasks WHERE run=? AND done=0', (active,)).fetchone()[0] if active else 0
        updates = db.execute('SELECT COUNT(*) FROM updates WHERE done=0').fetchone()[0]
        return pending, updates
    finally:
        db.close()


def _sync(root, running):
    result = {'status': 'not_initialized', 'scheduler_running': running, 'connections': [], 'truncated': False}
    path = root / 'data/github_sync/connections.sqlite'
    status = _path_status(root, path)
    if status == 'missing':
        return result
    result['status'] = 'unavailable'
    if status != 'present':
        return result
    deadline = time.monotonic() + 0.4
    db = None
    try:
        db = _readonly(root, path, deadline)
        rows = db.execute('SELECT repo, CASE WHEN length(value)<=? THEN value ELSE NULL END FROM connections ORDER BY repo LIMIT ?',
                          (_MAX_RECORD, _MAX_CONNECTIONS + 1)).fetchall()
        result['truncated'] = len(rows) > _MAX_CONNECTIONS
        for repository, raw in rows[:_MAX_CONNECTIONS]:
            if time.monotonic() >= deadline:
                result['truncated'] = True
                break
            if not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository):
                raise ValueError('invalid_local_state')
            value = _json_record(raw)
            if (not isinstance(value, dict) or value.get('repository') != repository
                    or value.get('phase') not in {'initial', 'updates', 'ready'}
                    or type(value.get('paused')) is not bool):
                raise ValueError('invalid_local_state')
            updated = value.get('updated_at')
            if updated is not None and (type(updated) not in {int, float} or not math.isfinite(updated) or updated < 0):
                raise ValueError('invalid_local_state')
            connection = {'repository': repository, 'phase': value['phase'], 'paused': value['paused'],
                          'pending_tasks': None, 'pending_updates': None, 'last_updated_at': updated, 'reason': None}
            try:
                connection['pending_tasks'], connection['pending_updates'] = _index_counts(root, repository, deadline)
            except Exception as error:
                connection['reason'] = str(error) if isinstance(error, ValueError) and str(error) in {
                    'index_not_initialized', 'unsafe_local_path'} else 'local_index_unavailable'
            if value.get('error'):
                connection['reason'] = 'sync_reported_error'
            result['connections'].append(connection)
        result['status'] = 'available'
    except Exception:
        result['connections'] = []
    finally:
        if db is not None:
            db.close()
    return result


def readiness_report(root, sync_running=False):
    """Inspect local metadata only. No .env, datasets, live services or ledgers."""
    if type(sync_running) is not bool:
        raise ValueError('Choose an explicit scheduler status')
    root = _root(root)
    runtime = prerequisites(root)
    groq, typesafe = _groq(root), _typesafe(root)
    cache = [{'id': identifier, 'status': _path_status(root, root / path, directory=True)} for identifier, path in (
        ('repositories', 'data/repos'), ('repository_index', 'data/repository_index'),
        ('repository_memory', 'artifacts/repository_memory'), ('saved_reports', 'artifacts/demo/runs'))]
    states = {row['id']: row['status'] for row in cache}
    def capability(identifier, status, reason):
        return {'id': identifier, 'status': status, 'reason': reason}
    capabilities = [capability('offline', 'available' if runtime['ready'] else 'blocked',
                               'runtime_ready' if runtime['ready'] else 'runtime_not_ready')]
    capabilities.append(capability('groq', 'conditional' if runtime['ready'] and groq['ready'] else 'blocked',
                                   'next_call_budget_unchecked' if runtime['ready'] and groq['ready'] else groq['reason'] if runtime['ready'] else 'runtime_not_ready'))
    jev_ready = runtime['ready'] and groq['ready'] and typesafe['ready']
    capabilities.append(capability('jev', 'conditional' if jev_ready else 'blocked',
                                   'next_call_budget_unchecked' if jev_ready else 'groq_not_ready' if not groq['ready'] else typesafe['reason'] if runtime['ready'] else 'runtime_not_ready'))
    for identifier, cache_id, available_reason, missing_reason in (
            ('repository_history', 'repository_index', 'local_index_metadata_only', 'repository_index_missing'),
            ('saved_reports', 'saved_reports', 'saved_reports_not_verified', 'no_saved_reports')):
        state = states[cache_id]
        capabilities.append(capability(identifier, 'conditional' if state == 'present' else 'blocked' if state == 'missing' else 'unknown',
                                       available_reason if state == 'present' else missing_reason if state == 'missing' else 'unsafe_local_path' if state == 'unsafe' else 'local_state_unavailable'))
    app_status, app_reason = _github_app(root)
    capabilities.append(capability('github_app', app_status, app_reason))
    capabilities.append(capability('batch', 'available' if runtime['ready'] else 'blocked',
                                   'offline_batch_available' if runtime['ready'] else 'runtime_not_ready'))
    # Source/job eligibility is deliberately not inspected by this global view.
    # The individual action preview remains authoritative for ownership, source
    # bindings, stage state, consent, authorization and its actual call budget.
    for identifier in ('questions', 'check_plan', 'claim_review'):
        ready = runtime['ready'] and groq['ready']
        capabilities.append(capability(identifier, 'conditional' if ready else 'blocked',
                                       'eligible_source_required' if ready else groq['reason'] if runtime['ready'] else 'runtime_not_ready'))
    capabilities.append(capability('handoff', 'conditional' if states['saved_reports'] == 'present' else 'unknown' if states['saved_reports'] not in {'present', 'missing'} else 'blocked',
                                   'eligible_source_required' if states['saved_reports'] == 'present' else 'no_saved_reports' if states['saved_reports'] == 'missing' else 'local_state_unavailable'))
    for identifier in ('stage_recovery', 'versions', 'releases', 'memory', 'followup', 'planned'):
        reason = ('eligible_recovery_source_required' if identifier == 'stage_recovery' else
                  'github_public_lookup_required' if identifier == 'releases' else
                  'product_source_required' if identifier == 'planned' else 'eligible_source_required')
        capabilities.append(capability(identifier, 'conditional' if runtime['ready'] else 'blocked',
                                       reason if runtime['ready'] else 'runtime_not_ready'))
    return {'schema': 'product_readiness_v1', 'checked_at': datetime.now(timezone.utc).isoformat(),
            'runtime': runtime, 'providers': {'groq': groq, 'typesafe': typesafe},
            'sync': _sync(root, sync_running), 'cache': cache, 'capabilities': capabilities,
            'model_calls': 0, 'evaluation_performed': False, 'live_services_verified': False}
