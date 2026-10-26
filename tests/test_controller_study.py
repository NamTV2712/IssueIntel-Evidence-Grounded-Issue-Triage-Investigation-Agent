"""Synthetic registration/replay only; no provider, key or benchmark input."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path

import pytest

from issueintel.evaluation import controller_study as study
from issueintel.investigation.pipeline import investigate
from issueintel.repository.graph import build_graph
from issueintel.repository.snapshot import build_snapshot
from test_planned_pipeline import planned_repository


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True) + '\n', encoding='utf-8')
    return sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def sources(tmp_path, planned_repository):
    root = tmp_path / 'study'; root.mkdir()
    (root / 'src/issueintel').mkdir(parents=True)
    (root / 'src/issueintel/synthetic.py').write_text('VALUE = 1\n', encoding='utf-8')
    config = root / 'configs'; config.mkdir()
    config.joinpath('runtime.toml').write_text('''provider = "groq"
model = "openai/gpt-oss-120b"
max_output_tokens = 4096
price_file = "configs/prices.json"
ledger = "data/runtime/budget.sqlite"
tokens_unlimited_authorized = true
usd_unlimited_authorized = true
''', encoding='utf-8')
    config.joinpath('typesafe.toml').write_text('''provider = "typesafe"
model = "jev-1.13.0"
enabled = false
daily_requests = 0
daily_usd = "0"
monthly_usd = "0"
price_file = "configs/typesafe_prices.json"
ledger = "data/runtime/typesafe.sqlite"
''', encoding='utf-8')
    save(config / 'author_decisions.json', {'model': 'openai/gpt-oss-120b',
        'scope': 'necessary_issueintel_rebuild_work', 'tokens_unlimited_authorized': True,
        'usd_unlimited_authorized': True})
    today = datetime.now(timezone.utc).date().isoformat()
    save(config / 'prices.json', {'checked_at': today, 'currency': 'USD',
        'source': 'https://console.groq.com/docs/model/openai/gpt-oss-120b',
        'models': {'openai/gpt-oss-120b': {'input_per_million': '0.15',
            'output_per_million': '0.60', 'context_tokens': 131072, 'max_output_tokens': 65536}}})
    save(config / 'typesafe_prices.json', {'checked_at': today, 'currency': 'USD',
        'source': 'https://docs.typesafe.ai/models', 'models': {'jev-1.13.0': {
            'input_per_million': '0.042', 'output_per_million': '0',
            'per_request_context': 64000, 'per_question_context': 32000}}})
    original, repository = planned_repository
    snapshot = build_snapshot(repository, original['revision'])
    graph = build_graph(snapshot)
    selection = {'schema': 'controller_study_selection_v1', 'purpose': study.PURPOSE,
                 'protocol_id': study.PROTOCOL_ID, 'cases': []}
    for number in range(1, 6):
        job_id = f'{number:024x}'
        issue = f'https://github.com/example/study/issues/{number}'
        case = {**original, 'case_id': issue, 'issue_url': issue,
                'repository': 'example/study', 'issue_numbers': [number]}
        pack = investigate(case, repository, snapshot=snapshot, graph=graph)
        name = 'artifacts/demo/runs/' + job_id + '/pack.json'
        fingerprint = save(root / name, pack)
        save(root / ('artifacts/demo/jobs/' + job_id + '.json'), {
            'id': job_id, 'status': 'completed', 'provider': 'none',
            'request': {'mode': 'github', 'issue_url': issue, 'depth': 'quick', 'provider': 'none'},
            'result': {'pack.json': {'path': name, 'sha256': fingerprint}}})
        selection['cases'].append({'unit_id': 'case-' + str(number), 'source_job_id': job_id,
                                  'pack': {'path': name, 'sha256': fingerprint}})
    selection_path = root / 'artifacts/selection.json'
    save(selection_path, selection)
    def loader(workspace, row, pack):
        authority_path = 'artifacts/demo/jobs/' + row['source_job_id'] + '.json'
        return {'case': pack['case'], 'snapshot': snapshot, 'graph': graph,
            'authority': {'path': authority_path, 'mode': 'github',
                          'sha256': sha256((workspace / authority_path).read_bytes()).hexdigest()}}
    return root, selection_path, selection, loader


def test_registration_complete_private_and_exact_replay(sources, monkeypatch):
    root, selected, selection, loader = sources
    import issueintel.runtime.io as runtime_io
    monkeypatch.setattr(runtime_io, 'load_local_env', lambda *a: pytest.fail('dotenv forbidden'))
    spec = study.register_study(root, selected, source_loader=loader)
    assert spec['schema'] == study.SCHEMA
    assert spec['study_id'] != spec['sha256']
    assert spec['bounds_are_authority'] is False
    loaded, directory = study.load_study(root, spec['study_id'], source_loader=loader)
    assert loaded == spec
    assert study.inspect_study(root, spec['study_id'], source_loader=loader)['valid']
    assert sorted(path.name for path in directory.iterdir()) == ['registration.json', 'selection.json']
    frozen = directory.joinpath('registration.json').read_text(encoding='utf-8')
    assert 'parser.parse_list crashes' not in frozen
    assert 'hypotheses' not in frozen
    for row in spec['cases']:
        assert row['product_policy']['recipe'] == study.product_study.RECIPE
        assert row['context']['case_sha256'] == row['product_policy']['context']['case_sha256']
    assert len(spec['budget_policy']['arms']) == 10
    assert spec['budget_policy']['max_owned_attempts'] == 110
    assert spec['budget_policy']['max_owned_micro_usd'] == 1935360
    assert {row['max_micro_usd'] for row in spec['budget_policy']['arms']} == {193536}
    assert {row['max_attempts'] for row in spec['budget_policy']['arms']} == {9, 13}
    assert [(row['provider'], row['max_attempts']) for row in spec['budget_policy']['providers']] == [('groq', 90), ('typesafe', 20)]


def test_native_workbench_replay_no_source_fetch_or_provider(sources, monkeypatch):
    root, selected, _, _ = sources
    import issueintel.investigation.workbench as workbench
    monkeypatch.setattr(workbench, 'fetch_github_issue', lambda *a, **k: pytest.fail('network forbidden'))
    monkeypatch.setattr(workbench, 'acquire_repository', lambda *a, **k: pytest.fail('network forbidden'))
    monkeypatch.setattr(workbench, 'GroqProvider', lambda *a, **k: pytest.fail('provider forbidden'))
    spec = study.register_study(root, selected)
    assert study.load_study(root, spec['study_id'])[0] == spec


def test_runtime_configs_preserve_raw_defaults_and_scope_both_ledgers(sources):
    root, selected, _, loader = sources
    before = (root / 'configs/typesafe.toml').read_bytes()
    spec = study.register_study(root, selected, source_loader=loader)
    groq, typesafe = study.runtime_configs(root, spec)
    directory = study.study_directory(root, spec['study_id'])
    assert groq.ledger_file == directory / 'native/groq.sqlite'
    assert typesafe.ledger_file == directory / 'native/typesafe.sqlite'
    assert typesafe.enabled and typesafe.daily_requests == 20
    assert str(typesafe.daily_usd) == str(typesafe.monthly_usd) == '0.053760'
    assert (root / 'configs/typesafe.toml').read_bytes() == before
    assert not (directory / 'native').exists()
    for provider, config in [('groq', groq), ('typesafe', typesafe)]:
        descriptor = study.runtime_config_descriptor(root, config, provider=provider, study_id=spec['study_id'])
        assert study.digest(descriptor) == spec['runtime'][provider]['derived_config_sha256']
        assert spec['runtime'][provider]['raw_config_sha256'] != study.digest(descriptor)
    with pytest.raises(ValueError, match='ledger'):
        study.runtime_config_descriptor(root, replace(groq, ledger_file=root / 'artifacts/other.sqlite'),
                                        provider='groq', study_id=spec['study_id'])


@pytest.mark.parametrize('mutation', ['count', 'duplicate_unit', 'duplicate_job', 'extra', 'protocol', 'hash', 'parent_path'])
def test_selection_rejects_malformed_or_widened_scope(sources, mutation):
    root, selected, selection, loader = sources
    if mutation == 'count': selection['cases'].pop()
    elif mutation == 'duplicate_unit': selection['cases'][1]['unit_id'] = selection['cases'][0]['unit_id']
    elif mutation == 'duplicate_job': selection['cases'][1]['source_job_id'] = selection['cases'][0]['source_job_id']
    elif mutation == 'extra': selection['cases'][0]['split'] = 'main_dev'
    elif mutation == 'protocol': selection['protocol_id'] = 'other'
    elif mutation == 'hash': selection['cases'][0]['pack']['sha256'] = 'bad'
    elif mutation == 'parent_path': selection['cases'][0]['pack']['path'] = 'artifacts/../raw.json'
    save(selected, selection)
    with pytest.raises(ValueError): study.register_study(root, selected, source_loader=loader)
    assert not (root / 'artifacts/controller-studies').exists()


@pytest.mark.parametrize('field', ['split', 'instance_id', 'case_id', 'followup', 'historical_source_audit'])
def test_source_case_forks_benchmarks_and_history_cannot_relabel(sources, field):
    root, selected, selection, loader = sources
    path = root / selection['cases'][0]['pack']['path']
    pack = json.loads(path.read_bytes())
    if field in {'followup', 'historical_source_audit'}: pack[field] = {}
    else: pack['case'][field] = 'main_dev' if field != 'case_id' else 'verified:example__study-1'
    selection['cases'][0]['pack']['sha256'] = save(path, pack)
    save(selected, selection)
    with pytest.raises(ValueError): study.register_study(root, selected, source_loader=loader)


def test_source_loader_cannot_fake_original_job_authority(sources):
    root, selected, selection, loader = sources
    row = selection['cases'][0]
    path = root / ('artifacts/demo/jobs/' + row['source_job_id'] + '.json')
    job = json.loads(path.read_bytes()); job['request']['mode'] = 'local'; save(path, job)
    with pytest.raises(ValueError, match='authority'):
        study.register_study(root, selected, source_loader=loader)


def test_source_mutation_and_missing_reported_separately(sources):
    root, selected, selection, loader = sources
    spec = study.register_study(root, selected, source_loader=loader)
    (root / 'src/issueintel/synthetic.py').write_text('VALUE = 2\n', encoding='utf-8')
    (root / selection['cases'][0]['pack']['path']).unlink()
    report = study.inspect_study(root, spec['study_id'], source_loader=loader)
    assert report['valid'] is False
    assert 'src/issueintel/synthetic.py' in report['stale']
    assert selection['cases'][0]['pack']['path'] in report['missing']
    assert report['provider_called'] is False
    with pytest.raises(ValueError): study.load_study(root, spec['study_id'], source_loader=loader)


def test_no_git_replay_still_checks_exact_pack_and_job_bytes(sources):
    root, selected, selection, loader = sources
    spec = study.register_study(root, selected, source_loader=loader)
    path = root / selection['cases'][0]['pack']['path']
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(ValueError, match='source bytes'):
        study.load_study(root, spec['study_id'], replay_sources=False,
                         source_loader=lambda *a: pytest.fail('Git replay disabled'))


def test_native_source_checks_incidental_database_alias_before_workbench(sources, monkeypatch):
    root, selected, _, _ = sources
    original = root / 'artifacts/original.sqlite'; original.write_bytes(b'synthetic private bytes')
    os.link(original, root / 'artifacts/demo/plans.sqlite')
    import issueintel.investigation.workbench as workbench
    monkeypatch.setattr(workbench, 'Workbench', lambda *a, **k: pytest.fail('unsafe constructor'))
    with pytest.raises(ValueError, match='hardlink'): study.register_study(root, selected)
    assert original.read_bytes() == b'synthetic private bytes'


def test_added_python_source_invalidates_full_fingerprint(sources):
    root, selected, _, loader = sources
    spec = study.register_study(root, selected, source_loader=loader)
    (root / 'src/new.py').write_text('# New runtime source\n', encoding='utf-8')
    with pytest.raises(ValueError, match='source code'):
        study.load_study(root, spec['study_id'], source_loader=loader)


def test_raw_selection_change_even_same_json_is_stale(sources):
    root, selected, _, loader = sources
    spec = study.register_study(root, selected, source_loader=loader)
    selected.write_bytes(selected.read_bytes() + b' ')
    with pytest.raises(ValueError, match='raw bytes'):
        study.load_study(root, spec['study_id'], source_loader=loader)


def test_config_and_price_mutation_fail_before_source_replay(sources):
    root, selected, _, loader = sources
    spec = study.register_study(root, selected, source_loader=loader)
    path = root / 'configs/prices.json'
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(ValueError):
        study.load_study(root, spec['study_id'], source_loader=lambda *a: pytest.fail('source replay after config mutation'))


def test_stale_prices_and_enabled_product_typesafe_denied(sources):
    root, selected, _, loader = sources
    path = root / 'configs/typesafe_prices.json'
    facts = json.loads(path.read_bytes()); facts['checked_at'] = '2000-01-01'; save(path, facts)
    with pytest.raises(RuntimeError): study.register_study(root, selected, source_loader=loader)
    facts['checked_at'] = datetime.now(timezone.utc).date().isoformat(); save(path, facts)
    path = root / 'configs/typesafe.toml'
    path.write_text(path.read_text().replace('enabled = false', 'enabled = true'), encoding='utf-8')
    with pytest.raises(RuntimeError): study.register_study(root, selected, source_loader=loader)


def test_registration_no_overwrite_and_self_seal_validation(sources):
    root, selected, _, loader = sources
    spec = study.register_study(root, selected, source_loader=loader)
    with pytest.raises(FileExistsError): study.register_study(root, selected, source_loader=loader)
    directory = study.study_directory(root, spec['study_id'])
    original = directory.joinpath('registration.json').read_bytes()
    with pytest.raises(FileExistsError): study.write_new(root, directory / 'registration.json', {'replacement': True})
    assert directory.joinpath('registration.json').read_bytes() == original
    changed = deepcopy(spec); changed['budget_policy']['max_owned_attempts'] += 1
    changed.pop('sha256'); save(directory / 'registration.json', study.seal(changed))
    with pytest.raises(ValueError, match='allowance'): study.load_study(root, spec['study_id'], source_loader=loader)


@pytest.mark.parametrize('raw', [b'{"a":1,"a":2}', b'{"x":NaN}', b'{"x":Infinity}', b'\xff', b'[' * 100 + b']' * 100])
def test_safe_json_rejects_duplicate_nonfinite_or_deep_values(tmp_path, raw):
    path = tmp_path / 'bad.json'; path.write_bytes(raw)
    with pytest.raises(ValueError): study.read_json(tmp_path, path)


def test_safe_file_io_bounded_scoped_and_hardlink_resistant(tmp_path):
    path = tmp_path / 'source.json'; path.write_bytes(b'{}')
    with pytest.raises(ValueError): study.read_json(path, maximum=1)
    with pytest.raises(ValueError): study.safe_path(tmp_path, '../outside.json')
    with pytest.raises(ValueError): study.safe_path(tmp_path, 'data/source.json', artifacts=True)
    alias = tmp_path / 'alias.json'; os.link(path, alias)
    with pytest.raises(ValueError, match='hardlink'): study.read_json(path)
    alias.unlink()
    assert study.inventory(tmp_path) == {'source.json': {'sha256': sha256(b'{}').hexdigest(), 'bytes': 2}}
    with pytest.raises(ValueError): study.inventory(tmp_path, maximum_bytes=1)


def test_link_or_junction_path_is_never_followed(tmp_path):
    outside = tmp_path / 'outside'; outside.mkdir()
    source = outside / 'secret.json'; source.write_text('{}', encoding='utf-8')
    root = tmp_path / 'workspace'; root.mkdir()
    link = root / 'linked'
    if os.name == 'nt':
        import subprocess
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)], capture_output=True)
        if result.returncode: pytest.skip('Junction creation unavailable')
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(ValueError, match='links|reparse'): study.read_json(root, link / 'secret.json')
        with pytest.raises(ValueError, match='links|reparse'): study.inventory(root)
    finally:
        if os.name == 'nt': os.rmdir(link)
        else: link.unlink()
    assert source.exists()
