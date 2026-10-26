"""Local readiness is metadata, never a live authentication/budget/data probe."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from issueintel.runtime import readiness
from issueintel.runtime.io import digest


def configured(root, *, typesafe=True):
    configs = root / 'configs'
    configs.mkdir(parents=True)
    source = root / 'src/issueintel/interfaces/web.py'
    source.parent.mkdir(parents=True)
    source.write_text('# synthetic source marker\n', encoding='utf-8')
    (configs / 'runtime.toml').write_text(
        'provider="groq"\nmodel="openai/gpt-oss-120b"\nmax_output_tokens=4096\n'
        'daily_total_tokens=100000\nmonthly_usd="2"\n'
        'price_file="configs/prices.json"\nledger="data/runtime/budget.sqlite"\n', encoding='utf-8')
    (configs / 'prices.json').write_text(json.dumps({
        'checked_at': datetime.now(timezone.utc).date().isoformat(), 'currency': 'USD',
        'source': 'https://console.groq.com/docs/models', 'models': {'openai/gpt-oss-120b': {
            'input_per_million': '0.15', 'output_per_million': '0.60',
            'context_tokens': 131072, 'max_output_tokens': 65536}}}), encoding='utf-8')
    (configs / 'typesafe.toml').write_text(
        'provider="typesafe"\nmodel="jev-1.13.0"\n'
        f'enabled={str(typesafe).lower()}\ndaily_requests=4\ndaily_usd="0.011"\nmonthly_usd="1"\n'
        'price_file="configs/typesafe_prices.json"\nledger="data/runtime/typesafe.sqlite"\n', encoding='utf-8')
    (configs / 'typesafe_prices.json').write_text(json.dumps({
        'checked_at': datetime.now(timezone.utc).date().isoformat(), 'currency': 'USD',
        'source': 'https://docs.typesafe.ai/models', 'models': {'jev-1.13.0': {
            'input_per_million': '0.042', 'output_per_million': '0',
            'per_request_context': 64000, 'per_question_context': 32000}}}), encoding='utf-8')
    return root


@pytest.fixture(autouse=True)
def isolated_metadata(monkeypatch):
    for key in ('GROQ_API_KEY', 'TYPESAFE_API_KEY', 'GITHUB_APP_ID', 'GITHUB_INSTALLATION_ID',
                'GITHUB_APP_PRIVATE_KEY_PATH', 'GITHUB_APP_CLIENT_ID', 'GITHUB_WEBHOOK_SECRET'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(readiness.shutil, 'which', lambda _: 'synthetic-git')
    monkeypatch.setattr(readiness.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0))


def connection_db(root, *, wal=False):
    path = root / 'data/github_sync/connections.sqlite'
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    if wal:
        assert db.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
    db.execute('CREATE TABLE connections(repo TEXT PRIMARY KEY, value TEXT)')
    db.commit()
    return path, db


def add_connection(db, repository='owner/repo', **changes):
    value = {'repository': repository, 'phase': 'ready', 'paused': False, 'updated_at': 1234, **changes}
    db.execute('INSERT INTO connections VALUES (?,?)', (repository, json.dumps(value)))
    db.commit()


def index_db(root, repository='owner/repo', *, wal=False):
    path = root / 'data/repository_index' / digest(repository)[:24] / 'index.sqlite'
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    if wal:
        assert db.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
    db.executescript('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);'
                     'CREATE TABLE tasks(run TEXT,done INTEGER);CREATE TABLE updates(done INTEGER);')
    db.executemany('INSERT INTO meta VALUES (?,?)', [('repository', json.dumps(repository)), ('active_run', '"run1"')])
    db.executemany('INSERT INTO tasks VALUES (?,?)', [('run1', 0), ('run1', 0), ('run1', 1), ('old', 0)])
    db.executemany('INSERT INTO updates VALUES (?)', [(0,), (1,)])
    db.commit()
    return path, db


def capability(report, identifier):
    return next(row for row in report['capabilities'] if row['id'] == identifier)


def test_pristine_root_does_not_initialize_data_or_read_env(tmp_path, monkeypatch):
    (tmp_path / '.env').write_text('GROQ_API_KEY=private-file-key\n', encoding='utf-8')
    original = Path.read_text
    def read_text(path, *args, **kwargs):
        assert path.name != '.env', 'Readiness must use loaded environment, not read secrets'
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', read_text)
    before = {path.relative_to(tmp_path) for path in tmp_path.rglob('*')}
    report = readiness.readiness_report(tmp_path)
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob('*')} == before
    assert not report['providers']['groq']['key_configured']
    assert report['sync'] == {'status': 'not_initialized', 'scheduler_running': False, 'connections': [], 'truncated': False}
    assert report['model_calls'] == 0 and not report['evaluation_performed'] and not report['live_services_verified']
    assert 'private-file-key' not in json.dumps(report)


def test_configuration_readiness_never_constructs_ledgers_or_live_clients(tmp_path, monkeypatch):
    configured(tmp_path)
    from issueintel.runtime.budget import BudgetLedger
    from issueintel.runtime.typesafe import _Ledger, TypesafeClient
    def forbidden(*a, **k):
        pytest.fail('Readiness initialized a ledger/provider')
    monkeypatch.setattr(BudgetLedger, '__init__', forbidden)
    monkeypatch.setattr(_Ledger, '__init__', forbidden)
    monkeypatch.setattr(TypesafeClient, '__init__', forbidden)
    monkeypatch.setenv('GROQ_API_KEY', 'groq-secret-example')
    monkeypatch.setenv('TYPESAFE_API_KEY', 'typesafe-secret-example')
    report = readiness.readiness_report(tmp_path)
    assert report['runtime']['ready']
    for provider in report['providers'].values():
        assert provider['ready'] and provider['reason'] == 'ready' and provider['price_date']
        assert not provider['budget_checked'] and not provider['live_verified']
    assert capability(report, 'groq')['status'] == capability(report, 'jev')['status'] == 'conditional'
    assert not (tmp_path / 'data').exists()
    rendered = json.dumps(report)
    assert 'groq-secret-example' not in rendered and 'typesafe-secret-example' not in rendered
    assert str(tmp_path) not in rendered


def test_per_function_capabilities_remain_source_conditional(tmp_path, monkeypatch):
    configured(tmp_path)
    monkeypatch.setenv('GROQ_API_KEY', 'synthetic')
    report = readiness.readiness_report(tmp_path)
    actions = {'questions', 'check_plan', 'claim_review', 'stage_recovery', 'versions',
               'releases', 'memory', 'followup', 'planned'}
    assert all(capability(report, identifier)['status'] == 'conditional' for identifier in actions)
    assert capability(report, 'stage_recovery')['reason'] == 'eligible_recovery_source_required'
    assert capability(report, 'handoff')['status'] == 'blocked'
    assert len({row['id'] for row in report['capabilities']}) == len(report['capabilities']) == 17


def test_benchmark_ledger_and_saved_report_contents_are_never_read(tmp_path, monkeypatch):
    configured(tmp_path)
    for relative in ('data/runtime/budget.sqlite', 'data/benchmark/main_test.jsonl',
                     'artifacts/demo/runs/saved/pack.json', 'artifacts/repository_memory/memory.sqlite'):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('private-content-must-not-be-read', encoding='utf-8')
    original = Path.open
    def guarded(path, *args, **kwargs):
        assert not path.is_relative_to(tmp_path / 'data'), 'Readiness read benchmark/ledger content'
        assert not path.is_relative_to(tmp_path / 'artifacts'), 'Readiness read a report/memory payload'
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', guarded)
    report = readiness.readiness_report(tmp_path)
    assert capability(report, 'handoff')['status'] == 'conditional'
    assert 'private-content-must-not-be-read' not in json.dumps(report)


@pytest.mark.parametrize('provider', ['groq', 'typesafe'])
@pytest.mark.parametrize('age', [31, -1])
def test_stale_and_future_prices_block(provider, age, tmp_path, monkeypatch):
    configured(tmp_path)
    monkeypatch.setenv('GROQ_API_KEY', 'synthetic')
    monkeypatch.setenv('TYPESAFE_API_KEY', 'synthetic')
    path = tmp_path / 'configs' / ('prices.json' if provider == 'groq' else 'typesafe_prices.json')
    row = json.loads(path.read_text())
    row['checked_at'] = (datetime.now(timezone.utc).date() - timedelta(days=age)).isoformat()
    path.write_text(json.dumps(row), encoding='utf-8')
    assert readiness.readiness_report(tmp_path)['providers'][provider]['reason'] == 'invalid_or_stale_prices'


def test_disabled_typesafe_does_not_become_ready_with_key(tmp_path, monkeypatch):
    configured(tmp_path, typesafe=False)
    monkeypatch.setenv('TYPESAFE_API_KEY', 'synthetic')
    result = readiness.readiness_report(tmp_path)['providers']['typesafe']
    assert result['key_configured'] and not result['ready'] and result['reason'] == 'disabled'


def test_missing_key_is_reported_but_price_metadata_is_retained(tmp_path):
    configured(tmp_path)
    report = readiness.readiness_report(tmp_path)
    assert all(row['reason'] == 'missing_api_key' and row['price_date'] for row in report['providers'].values())


def test_output_cap_must_leave_context_for_positive_input(tmp_path, monkeypatch):
    configured(tmp_path)
    monkeypatch.setenv('GROQ_API_KEY', 'synthetic')
    path = tmp_path / 'configs/prices.json'
    facts = json.loads(path.read_text(encoding='utf-8'))
    facts['models']['openai/gpt-oss-120b'].update(context_tokens=4096,max_output_tokens=4096)
    path.write_text(json.dumps(facts),encoding='utf-8')
    provider = readiness.readiness_report(tmp_path)['providers']['groq']
    assert provider['ready'] is False and provider['reason'] == 'invalid_per_call_bounds'


@pytest.mark.parametrize('mutate', ['unknown_provider', 'malformed', 'oversized', 'missing_authority', 'per_call'])
def test_bad_groq_configuration_has_only_stable_codes(mutate, tmp_path, monkeypatch):
    configured(tmp_path)
    monkeypatch.setenv('GROQ_API_KEY', 'synthetic')
    path = tmp_path / 'configs/runtime.toml'
    value = path.read_text()
    expected = 'invalid_configuration'
    if mutate == 'unknown_provider':
        value = value.replace('provider="groq"', 'provider="private-provider-string"')
    elif mutate == 'malformed':
        value = '[private-malformed-text'
    elif mutate == 'oversized':
        value = '#' + 'x' * (readiness._MAX_CONFIG + 1)
    elif mutate == 'missing_authority':
        value += '\ntokens_unlimited_authorized=true\n'
    else:
        value = value.replace('max_output_tokens=4096', 'max_output_tokens=999999')
        expected = 'invalid_per_call_bounds'
    path.write_text(value, encoding='utf-8')
    report = readiness.readiness_report(tmp_path)
    assert report['providers']['groq']['reason'] == expected
    assert 'private-provider-string' not in json.dumps(report) and 'private-malformed-text' not in json.dumps(report)


def test_cache_presence_and_app_are_conditional_metadata(tmp_path, monkeypatch):
    configured(tmp_path)
    for path in ('data/repos', 'data/repository_index', 'artifacts/repository_memory', 'artifacts/demo/runs'):
        (tmp_path / path).mkdir(parents=True)
    monkeypatch.setenv('GITHUB_APP_ID', '123')
    monkeypatch.setenv('GITHUB_WEBHOOK_SECRET', 'webhook-synthetic-secret')
    report = readiness.readiness_report(tmp_path)
    assert all(row['status'] == 'present' for row in report['cache'])
    assert capability(report, 'saved_reports')['status'] == 'conditional'
    assert capability(report, 'repository_history')['status'] == 'conditional'
    assert capability(report, 'github_app') == {'id': 'github_app', 'status': 'blocked', 'reason': 'app_private_key_missing'}
    assert 'webhook-synthetic-secret' not in json.dumps(report)


def app_configuration(root, monkeypatch, *, key=None):
    key = key or root / 'synthetic-app-key.pem'
    key.write_bytes(b'SYNTHETIC_METADATA_ONLY_NOT_A_VALID_PEM')
    monkeypatch.setenv('GITHUB_APP_ID', '123')
    monkeypatch.setenv('GITHUB_APP_PRIVATE_KEY_PATH', str(key))
    monkeypatch.setenv('GITHUB_WEBHOOK_SECRET', 'synthetic-webhook-do-not-display')
    monkeypatch.setattr(readiness, '_app_dependencies_present', lambda: True)
    return key


def test_configured_app_metadata_can_be_external_but_never_claims_authentication(tmp_path, monkeypatch):
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as outside:
        key = app_configuration(tmp_path, monkeypatch, key=Path(outside) / 'private-name.pem')
        def denied(*a, **k):
            pytest.fail('App readiness must not read key bytes, sign or mint')
        for method in ('open', 'read_bytes', 'read_text'):
            monkeypatch.setattr(Path, method, denied)
        from issueintel.integrations import github_app
        monkeypatch.setattr(github_app.GitHubAppAuth, '__init__', denied)
        assert readiness._github_app(tmp_path) == ('unknown', 'app_https_permissions_not_verified')
        # A deliberately invalid PEM is indistinguishable at metadata-only scope.
        assert str(key) not in json.dumps(readiness._github_app(tmp_path))


@pytest.mark.parametrize('field,value,reason', [
    ('GITHUB_APP_ID', '', 'app_id_missing'),
    ('GITHUB_APP_ID', '0', 'app_configuration_invalid'),
    ('GITHUB_APP_ID', '9223372036854775808', 'app_configuration_invalid'),
    ('GITHUB_APP_ID', 'private-malformed-app', 'app_configuration_invalid'),
    ('GITHUB_INSTALLATION_ID', 'false', 'app_configuration_invalid'),
    ('GITHUB_INSTALLATION_ID', '0', 'app_configuration_invalid'),
    ('GITHUB_APP_CLIENT_ID', 'invalid client', 'app_configuration_invalid'),
    ('GITHUB_APP_PRIVATE_KEY_PATH', '', 'app_private_key_missing'),
    ('GITHUB_APP_PRIVATE_KEY_PATH', 'secret\npath', 'app_configuration_invalid'),
    ('GITHUB_WEBHOOK_SECRET', '', 'app_webhook_secret_missing'),
])
def test_known_app_configuration_blockers_are_local_and_sanitized(tmp_path, monkeypatch, field, value, reason):
    key = app_configuration(tmp_path, monkeypatch)
    monkeypatch.setenv(field, value)
    status = readiness._github_app(tmp_path)
    assert status == ('blocked', reason)
    serialized = json.dumps(status)
    assert str(key) not in serialized and 'private-malformed-app' not in serialized
    assert 'synthetic-webhook-do-not-display' not in serialized and 'secret\\npath' not in serialized


def test_optional_client_and_installation_are_not_required_for_app_signing_metadata(tmp_path, monkeypatch):
    app_configuration(tmp_path, monkeypatch)
    assert readiness._github_app(tmp_path) == ('unknown', 'app_https_permissions_not_verified')
    monkeypatch.setenv('GITHUB_INSTALLATION_ID', '77')
    monkeypatch.setenv('GITHUB_APP_CLIENT_ID', 'Iv1.synthetic')
    assert readiness._github_app(tmp_path) == ('unknown', 'app_https_permissions_not_verified')


@pytest.mark.parametrize('kind,reason', [
    ('missing', 'app_private_key_missing'), ('directory', 'app_private_key_unavailable'),
    ('empty', 'app_private_key_unavailable'), ('oversized', 'app_private_key_unavailable'),
    ('hardlink', 'app_private_key_unsafe'),
])
def test_app_key_metadata_shape_blocks_without_reading_contents(tmp_path, monkeypatch, kind, reason):
    key = app_configuration(tmp_path, monkeypatch)
    if kind == 'hardlink':
        import os
        os.link(key, key.with_name('alias.pem'))
    else:
        key.unlink()
        if kind == 'directory': key.mkdir()
        elif kind == 'empty': key.write_bytes(b'')
        elif kind == 'oversized': key.write_bytes(b'x'*65537)
    monkeypatch.setattr(Path, 'open', lambda *a, **k: pytest.fail('Key contents were opened'))
    assert readiness._github_app(tmp_path) == ('blocked', reason)


def test_app_key_reparse_ancestor_is_rejected_before_descending(tmp_path, monkeypatch):
    key = app_configuration(tmp_path, monkeypatch)
    native = Path.lstat
    visited = []
    def marked(path):
        visited.append(path)
        value = native(path)
        if path == key.parent:
            return SimpleNamespace(st_mode=value.st_mode, st_file_attributes=0x400)
        return value
    monkeypatch.setattr(Path, 'lstat', marked)
    assert readiness._github_app(tmp_path) == ('blocked', 'app_private_key_unsafe')
    assert key not in visited


def test_relative_app_key_is_resolved_against_project_root(tmp_path, monkeypatch):
    key = app_configuration(tmp_path, monkeypatch)
    monkeypatch.setenv('GITHUB_APP_PRIVATE_KEY_PATH', key.name)
    assert readiness._github_app(tmp_path) == ('unknown', 'app_https_permissions_not_verified')


def test_unc_app_key_is_blocked_without_network_filesystem_lookup(tmp_path, monkeypatch):
    app_configuration(tmp_path, monkeypatch)
    monkeypatch.setenv('GITHUB_APP_PRIVATE_KEY_PATH', '//issueintel-synthetic.invalid/share/private.pem')
    monkeypatch.setattr(Path, 'lstat', lambda *a: pytest.fail('UNC filesystem lookup attempted'))
    assert readiness._github_app(tmp_path) == ('blocked', 'app_private_key_unsafe')


def test_app_metadata_access_error_does_not_expose_path_or_exception(tmp_path, monkeypatch):
    key = app_configuration(tmp_path, monkeypatch)
    def denied(*a):
        raise PermissionError('synthetic-secret-and-path-'+str(key))
    monkeypatch.setattr(Path, 'lstat', denied)
    result = readiness._github_app(tmp_path)
    assert result == ('blocked', 'app_private_key_unavailable')
    assert 'synthetic-secret' not in json.dumps(result) and str(key) not in json.dumps(result)


@pytest.mark.parametrize('missing', ['jwt', 'cryptography'])
def test_missing_app_signing_dependency_has_actionable_blocker_without_import(tmp_path, monkeypatch, missing):
    import importlib.util
    # Restore the real metadata-only checker after creating synthetic key config.
    check = readiness._app_dependencies_present
    app_configuration(tmp_path, monkeypatch)
    monkeypatch.setattr(readiness, '_app_dependencies_present', check)
    seen = []
    def spec(name):
        seen.append(name)
        assert name in {'jwt', 'cryptography'}
        return None if name == missing else SimpleNamespace()
    monkeypatch.setattr(importlib.util, 'find_spec', spec)
    assert readiness._github_app(tmp_path) == ('blocked', 'app_signing_dependencies_missing')
    assert missing in seen


def test_dependency_discovery_failure_is_a_safe_blocker(tmp_path, monkeypatch):
    import importlib.util
    check = readiness._app_dependencies_present
    app_configuration(tmp_path, monkeypatch)
    monkeypatch.setattr(readiness, '_app_dependencies_present', check)
    def failed(name):
        raise RuntimeError('synthetic-private-import-hook-detail')
    monkeypatch.setattr(importlib.util, 'find_spec', failed)
    assert readiness._github_app(tmp_path) == ('blocked', 'app_signing_dependencies_missing')


def test_sync_existing_regular_database_is_read_only(tmp_path):
    path, db = connection_db(tmp_path)
    add_connection(db)
    db.close()
    index_path, index = index_db(tmp_path)
    index.close()
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    sync = readiness.readiness_report(tmp_path, sync_running=True)['sync']
    assert sync['status'] == 'available' and sync['scheduler_running'] and not sync['truncated']
    assert sync['connections'] == [{'repository': 'owner/repo', 'phase': 'ready', 'paused': False,
                                     'pending_tasks': 2, 'pending_updates': 1, 'last_updated_at': 1234, 'reason': None}]
    assert {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before


def test_sync_sees_committed_wal_without_new_sidecars(tmp_path):
    path, db = connection_db(tmp_path, wal=True)
    index_path, index = index_db(tmp_path, wal=True)
    try:
        add_connection(db)
        files = {p.relative_to(tmp_path) for p in tmp_path.rglob('*')}
        original = path.read_bytes(), index_path.read_bytes()
        result = readiness.readiness_report(tmp_path)['sync']
        assert result['status'] == 'available' and result['connections'][0]['pending_tasks'] == 2
        assert {p.relative_to(tmp_path) for p in tmp_path.rglob('*')} == files
        assert (path.read_bytes(), index_path.read_bytes()) == original
    finally:
        db.close()
        index.close()


def test_clean_wal_database_missing_sidecars_stays_unknown_without_creating_them(tmp_path):
    path, db = connection_db(tmp_path, wal=True)
    add_connection(db)
    db.close()
    assert not Path(str(path) + '-wal').exists()
    before = {p.relative_to(tmp_path) for p in tmp_path.rglob('*')}
    assert readiness.readiness_report(tmp_path)['sync']['status'] == 'unavailable'
    assert {p.relative_to(tmp_path) for p in tmp_path.rglob('*')} == before


def test_sync_missing_index_preserves_unknown_counts_and_raw_errors_are_hidden(tmp_path):
    path, db = connection_db(tmp_path)
    add_connection(db, error='https://secret-in-error.example/private-token')
    db.close()
    result = readiness.readiness_report(tmp_path)['sync']
    assert result['connections'][0]['pending_tasks'] is None
    assert result['connections'][0]['reason'] == 'sync_reported_error'
    assert 'secret-in-error' not in json.dumps(result)
    assert not (tmp_path / 'data/repository_index').exists()


@pytest.mark.parametrize('bad', ['corrupt', 'schema', 'record', 'identity', 'oversized'])
def test_unreadable_sync_state_never_claims_available(bad, tmp_path):
    path, db = connection_db(tmp_path)
    if bad == 'schema':
        db.execute('DROP TABLE connections')
    elif bad == 'record':
        add_connection(db, phase='secret-invalid-phase')
    elif bad == 'identity':
        db.execute('INSERT INTO connections VALUES (?,?)', ('owner/repo', '{"repository":"wrong/repo"}'))
    elif bad == 'oversized':
        db.execute('INSERT INTO connections VALUES (?,?)', ('owner/repo', 'x' * (readiness._MAX_RECORD + 1)))
    db.commit()
    db.close()
    if bad == 'corrupt':
        path.write_bytes(b'not a database')
    result = readiness.readiness_report(tmp_path)['sync']
    assert result['status'] == 'unavailable' and result['connections'] == []
    assert 'secret-invalid-phase' not in json.dumps(result)


def test_sync_listing_is_bounded(tmp_path):
    path, db = connection_db(tmp_path)
    for number in range(readiness._MAX_CONNECTIONS + 3):
        add_connection(db, f'owner/repo{number}')
    db.close()
    sync = readiness.readiness_report(tmp_path)['sync']
    assert sync['status'] == 'available' and sync['truncated']
    assert 0 < len(sync['connections']) <= readiness._MAX_CONNECTIONS


def test_query_deadline_can_abort_without_changing_the_database(tmp_path, monkeypatch):
    path, db = connection_db(tmp_path)
    add_connection(db)
    db.close()
    original = path.read_bytes()
    counter = [0]
    def elapsed():
        counter[0] += 1
        return float(counter[0])
    monkeypatch.setattr(readiness.time, 'monotonic', elapsed)
    result = readiness.readiness_report(tmp_path)['sync']
    assert result['truncated'] or result['status'] == 'unavailable'
    assert path.read_bytes() == original


def test_reparse_ancestor_is_rejected_before_any_child_read(tmp_path, monkeypatch):
    configured(tmp_path)
    original = Path.lstat
    def reparse(path):
        value = original(path)
        if path == tmp_path / 'configs':
            return SimpleNamespace(st_mode=value.st_mode, st_file_attributes=readiness._REPARSE)
        assert path.parent != tmp_path / 'configs', 'Readiness traversed reparse ancestor'
        return value
    monkeypatch.setattr(Path, 'lstat', reparse)
    report = readiness.readiness_report(tmp_path)
    assert not report['runtime']['source_complete']
    assert all(value['reason'] == 'unsafe_configuration_path' for value in report['providers'].values())


def test_linked_cache_root_is_never_followed(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + '-outside')
    outside.mkdir()
    (tmp_path / 'data').mkdir()
    try:
        (tmp_path / 'data/repos').symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('Creating synthetic symlink is not permitted on this host')
    report = readiness.readiness_report(tmp_path)
    assert next(row for row in report['cache'] if row['id'] == 'repositories')['status'] == 'unsafe'


def test_explicit_boolean_scheduler_policy(tmp_path):
    with pytest.raises(ValueError):
        readiness.readiness_report(tmp_path, sync_running=1)


def test_prerequisite_failures_are_stable_and_independent(tmp_path, monkeypatch):
    configured(tmp_path)
    monkeypatch.setattr(readiness.shutil, 'which', lambda _: None)
    original = readiness.sqlite3.connect
    def no_fts(path, *args, **kwargs):
        if path == ':memory:':
            raise sqlite3.DatabaseError('synthetic-private-error')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(readiness.sqlite3, 'connect', no_fts)
    report = readiness.prerequisites(tmp_path)
    assert report['source_complete'] and not report['ready']
    assert report['problems'] == ['git_missing', 'fts5_unavailable']
    assert not report['git_available'] and not report['fts5_available']


def test_git_process_failure_is_not_reported_as_available(tmp_path, monkeypatch):
    configured(tmp_path)
    monkeypatch.setattr(readiness.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1))
    report = readiness.prerequisites(tmp_path)
    assert report['problems'] == ['git_unavailable'] and not report['git_available']
