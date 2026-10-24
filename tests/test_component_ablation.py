"""Synthetic registration and admission tests; no dataset, environment or API."""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess

import pytest

from issueintel.benchmarks import component_ablation as experiment
from issueintel.repository.snapshot import RepositorySnapshot, SourceFile
from issueintel.runtime.io import digest


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(experiment._bytes(value))
    return path


def source(root, cid, repository):
    cutoff = '2020-01-03T00:00:00Z'
    commit = 'a' * 40
    proof_hash = 'b' * 64
    case = {'case_id': cid, 'instance_id': cid.split(':')[1], 'split': 'main_dev',
            'repository': repository, 'revision': commit, 'issue_numbers': [99],
            'issue_url': f'https://github.com/{repository}/issues/99',
            'problem_statement': 'parser fails on an empty value', 'issue_created_at': cutoff}
    proof = {'commit_sha': commit, 'verified': True, 'source_id': 'https://example.invalid/push',
             'available_at': '2020-01-01T00:00:00Z', 'strict_dev_proof_sha256': proof_hash}
    text = 'def parse(value):\n    return value[0]\n'
    payload = {'repository': str(root), 'commit_sha': commit, 'tree_sha': 'c' * 40,
               'files': (SourceFile('parser.py', text, sha256(text.encode()).hexdigest()),),
               'protocol': 'issue_time', 'cutoff': cutoff,
               'availability': {'status': 'caller_verified_receipt', 'historical_certified': True,
                   'source_id': proof['source_id'], 'available_at': proof['available_at'],
                   'receipt_sha256': digest(proof), 'receipt': proof},
               'skipped': (), 'committer_time': '2019-01-01T00:00:00Z', 'snapshot_sha256': ''}
    snapshot = RepositorySnapshot(**payload)
    unsealed = asdict(snapshot); unsealed.pop('snapshot_sha256')
    snapshot = replace(snapshot, snapshot_sha256=digest(unsealed))
    query = {'schema': 'main_dev_original_query_receipt_v1', 'verified': True,
             'case_id': cid, 'created_at': cutoff,
             'text_sha256': sha256(case['problem_statement'].encode()).hexdigest(),
             'strict_dev_proof_sha256': proof_hash, 'source_urls': [case['issue_url']]}
    return case, snapshot, query


def historical(repository='synthetic/a', number=1, *, body='Prior observed symptom'):
    created = '2020-01-01T00:00:00Z'
    issue = {'number': number, 'url': f'https://api.github.com/repos/{repository}/issues/{number}',
             'created_at': created, 'updated_at': created, 'title': 'Prior issue', 'body': body}
    text = issue['title'] + '\n' + issue['body'] + '\n'
    return {'source_url': issue['url'], 'fetched_at': '2026-10-12T00:00:00Z', 'payload': issue,
            'body_provenance': {'kind': 'issue_creation_event', 'available_at': created,
                'source_url': 'https://data.gharchive.org/2020-01-01-0.json.gz',
                'content_sha256': sha256(text.encode()).hexdigest(), 'history_complete': True,
                'payload': {'type': 'IssuesEvent', 'public': True, 'repo': {'name': repository},
                    'created_at': created, 'payload': {'action': 'opened', 'issue': deepcopy(issue)}}}}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path
    (root / 'src/issueintel').mkdir(parents=True)
    (root / 'src/issueintel/synthetic.py').write_text('VERSION = 1\n', encoding='utf-8')
    configs = root / 'configs'; configs.mkdir()
    (configs / 'runtime.toml').write_text(
        'provider = "groq"\nmodel = "openai/gpt-oss-120b"\nmax_output_tokens = 4096\n'
        'daily_total_tokens = 100000\nmonthly_usd = 2\n'
        'price_file = "configs/prices.json"\nledger = "artifacts/private-ledger.sqlite"\n', encoding='utf-8')
    write(configs / 'prices.json', {
        'checked_at': datetime.now(timezone.utc).date().isoformat(), 'currency': 'USD',
        'source': 'https://console.groq.com/docs/models',
        'models': {'openai/gpt-oss-120b': {'input_per_million': '0.1', 'output_per_million': '0.2',
                    'context_tokens': 131072, 'max_output_tokens': 32768}}})
    write(configs / 'component_ablation_protocol.json', experiment.proposed_protocol())
    rows = [{'case_id': f'verified:synthetic__case-{i:02d}', 'repository': f'synthetic/{"abc"[i % 3]}'}
            for i in reversed(range(30))]
    catalog = {'cases': rows, 'checkpoint_sha256': 'd' * 64}
    contexts = {row['case_id']: source(root, row['case_id'], row['repository']) for row in rows}
    calls = []
    def accepted(_root, cid):
        calls.append(cid)
        return contexts[cid]
    monkeypatch.setattr(experiment, 'public_catalog', lambda root: deepcopy(catalog))
    monkeypatch.setattr(experiment, 'accepted_context', accepted)
    monkeypatch.setattr(experiment.RuntimeConfig, 'ledger', lambda *a, **k: pytest.fail('ledger constructed'))
    monkeypatch.setattr('socket.create_connection', lambda *a, **k: pytest.fail('network called'))
    monkeypatch.setattr('subprocess.Popen', lambda *a, **k: pytest.fail('process started'))
    return root, catalog, contexts, calls


def registered(workspace, *, history=None):
    root = workspace[0]
    path = write(root / 'artifacts/history-source.json', history) if history is not None else None
    result = experiment.register_component_ablation(root, history_path=path)
    spec, directory = experiment.load_registration(root, result['experiment_sha256'])
    return spec, directory, result


def authorization(root, spec, *, count=1):
    value = {'schema': experiment.AUTHORIZATION_SCHEMA, 'scope': experiment.SCOPE,
        'registration_sha256': spec['sha256'], 'code_fingerprint_sha256': digest(spec['fingerprint']),
        'protocol_sha256': spec['protocol_sha256'], 'case_ids': [row['case_id'] for row in spec['cases']],
        'provider': 'groq', 'model': spec['runtime']['model'],
        'reasoning_effort': spec['runtime']['reasoning_effort'],
        'max_output_tokens': spec['runtime']['max_output_tokens'],
        'max_provider_calls_per_arm': 5, 'max_provider_calls_total': count * 25,
        'authorized_case_count': count, 'phase': 'first_case_pilot' if count == 1 else 'registered_matrix',
        'author_request': 'Synthetic fixture declaration only', 'date': '2026-10-24',
        'execution_authorized': True, 'gold_scoring_authorized': False,
        **{flag: False for flag in experiment.ACCESS_FLAGS}}
    return value, write(root / 'artifacts/fixture-authorization.json', value)


def test_registration_and_preflight_use_only_exact_public_sources_and_write_no_ledger(workspace):
    root, catalog, _, calls = workspace
    spec, directory, result = registered(workspace)
    selected = experiment._selected_rows(catalog)
    assert len(spec['cases']) == len(selected) == result['selected_cases'] == 25
    assert [row['repository'] for row in selected[:3]] == ['synthetic/a', 'synthetic/b', 'synthetic/c']
    assert [row['case_id'] for row in spec['cases']] == [row['case_id'] for row in selected]
    assert len(calls) == 25 and len(set(calls)) == 25
    assert spec['execution_authorized'] is spec['gold_read'] is spec['quality_claim'] is False
    assert list(row['arm_id'] for row in spec['arms']) == list(experiment.ARMS)
    assert not (root / 'artifacts/private-ledger.sqlite').exists()
    preflight = experiment.preflight_component_ablation(root, spec['sha256'])
    assert preflight['source_replay_valid'] and not preflight['provider_or_ledger_constructed']
    assert not preflight['history_contrast_informative']
    assert set(path.name for path in directory.iterdir()) == {'registration.json', 'history-input.json', 'protocol.json'}
    assert not (root / '.env').exists()


def test_existing_environment_and_restricted_files_are_never_opened(workspace, monkeypatch):
    root = workspace[0]
    (root / '.env').write_text('PRIVATE_CANARY=never_read\n')
    restricted = root / 'data/restricted'; restricted.mkdir(parents=True)
    (restricted / 'gold.json').write_text('{"private":"never read"}')
    original = Path.open
    def guard(path, *args, **kwargs):
        if path.name == '.env' or restricted in path.parents:
            pytest.fail('Private environment or restricted gold opened')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', guard)
    spec, _, _ = registered(workspace)
    experiment.preflight_component_ablation(root, spec['sha256'])
    assert experiment.inspect_component_ablation(root, spec['sha256'])['source_replay_valid']


def test_history_is_frozen_with_independent_prior_proof_and_reopened_without_original(workspace):
    root, catalog, _, _ = workspace
    selected = experiment._selected_rows(catalog)
    cid = selected[0]['case_id']
    mapping = {cid: [historical(selected[0]['repository'])]}
    spec, directory, result = registered(workspace, history=mapping)
    (root / 'artifacts/history-source.json').unlink()
    loaded = experiment.load_history_sources(root, spec, directory)
    assert loaded[cid] == mapping[cid] and len(loaded) == 25
    assert result['history_covered_cases'] == 1 and result['history_contrast_informative']
    assert spec['cases'][0]['history_included_sources'] == 1
    assert experiment.preflight_component_ablation(root, spec['sha256'])['history_covered_cases'] == 1


def test_unproved_history_keeps_case_with_zero_coverage(workspace):
    row = experiment._selected_rows(workspace[1])[0]
    item = historical(row['repository']); item.pop('body_provenance')
    spec, _, result = registered(workspace, history={row['case_id']: [item]})
    assert len(spec['cases']) == 25 and result['history_covered_cases'] == 0
    assert spec['cases'][0]['history_selected_sources'] == 1


def test_history_can_exceed_two_mib_total_but_not_per_case(workspace):
    rows = experiment._selected_rows(workspace[1])
    # Two bounded case inputs; each receipt remains below 512KiB including its proof copy.
    mapping = {row['case_id']: [historical(row['repository'], i, body='x' * 180000) for i in range(1, 5)]
               for row in rows[:2]}
    spec, _, result = registered(workspace, history=mapping)
    assert spec['history_input']['bytes'] > experiment.MAX_JSON_BYTES
    assert result['history_covered_cases'] == 2


@pytest.mark.parametrize('mapping', [
    {'verified:unselected': []}, {'main_test:hidden': []}, [],
    {'verified:synthetic__case-00': [{'patch': 'not permitted'}]},
    {'verified:synthetic__case-00': [None] * 21},
    {'verified:synthetic__case-00': 'not a receipt list'},
    {'verified:synthetic__case-00': [{'body': 'x' * (2 * 1024 * 1024)}]},
])
def test_history_invalid_selections_fail_before_registration_publish(workspace, mapping):
    root = workspace[0]
    history = write(root / 'artifacts/input.json', mapping)
    with pytest.raises(ValueError):
        experiment.register_component_ablation(root, history_path=history)
    assert not (root / 'artifacts/experiments').exists()


@pytest.mark.parametrize('change', [
    lambda x: x.update(execution_authorized=True),
    lambda x: x.update(holdout_access_authorized=True),
    lambda x: x.update(selected_case_count=26),
    lambda x: x.update(trials_per_case_arm=True),
    lambda x: x['arms'][1].update(graph=False),
    lambda x: x['limits'].update(max_provider_calls_per_arm=6),
    lambda x: x.update(unknown='extra'),
    lambda x: x.update(max_output_tokens=True),
])
def test_unsupported_protocol_is_not_an_authorization_or_arbitrary_experiment(workspace, change):
    root = workspace[0]
    value = experiment.proposed_protocol(); change(value)
    write(root / 'configs/component_ablation_protocol.json', value)
    with pytest.raises(ValueError):
        experiment.register_component_ablation(root)
    assert workspace[3] == []


@pytest.mark.parametrize('relative', ['src/issueintel/synthetic.py', 'configs/runtime.toml', 'configs/prices.json'])
def test_code_budget_or_price_edits_reject_load_but_inspection_reports_stale(workspace, relative):
    spec, _, _ = registered(workspace)
    path = workspace[0] / relative
    path.write_bytes(path.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='configuration changed'):
        experiment.load_registration(workspace[0], spec['sha256'])
    inspection = experiment.inspect_component_ablation(workspace[0], spec['sha256'])
    assert not inspection['source_replay_valid'] and inspection['preflight_error_type'] == 'ValueError'
    assert inspection['quality_claim'] is inspection['execution_authorized'] is False


def test_added_source_file_is_part_of_fingerprint(workspace):
    spec, _, _ = registered(workspace)
    (workspace[0] / 'src/issueintel/new.py').write_text('NEW = 1\n')
    with pytest.raises(ValueError, match='configuration changed'):
        experiment.load_registration(workspace[0], spec['sha256'])


@pytest.mark.parametrize('change', [
    lambda case, snap, receipt: (dict(case, problem_statement='changed query'), snap, receipt),
    lambda case, snap, receipt: (case, snap, dict(receipt, source_urls=['https://example.invalid/changed'])),
    lambda case, snap, receipt: (dict(case, split='main_test'), snap, receipt),
    lambda case, snap, receipt: (case, snap, dict(receipt, verified=False)),
    lambda case, snap, receipt: (case, snap, dict(receipt, strict_dev_proof_sha256='f' * 64)),
])
def test_context_source_or_proof_changes_reject_preflight(workspace, change):
    spec, _, _ = registered(workspace)
    cid = spec['cases'][0]['case_id']
    workspace[2][cid] = change(*workspace[2][cid])
    with pytest.raises(ValueError):
        experiment.preflight_component_ablation(workspace[0], spec['sha256'])


def test_graph_parser_and_catalog_checkpoint_changes_reject_preflight(workspace, monkeypatch):
    spec, _, _ = registered(workspace)
    builder = experiment.build_graph
    monkeypatch.setattr(experiment, 'build_graph', lambda snapshot: replace(builder(snapshot), parser_version='changed'))
    with pytest.raises(ValueError, match='graph or historical proof changed'):
        experiment.preflight_component_ablation(workspace[0], spec['sha256'])
    monkeypatch.setattr(experiment, 'build_graph', builder)
    workspace[1]['checkpoint_sha256'] = 'e' * 64
    with pytest.raises(ValueError, match='membership or checkpoint changed'):
        experiment.preflight_component_ablation(workspace[0], spec['sha256'])


def test_catalog_change_and_mutation_during_registration_are_rejected(workspace, monkeypatch):
    root, catalog, contexts, _ = workspace
    original = experiment.accepted_context
    def mutate(root, cid):
        result = original(root, cid)
        (root / 'src/issueintel/synthetic.py').write_text('VERSION = 2\n')
        return result
    monkeypatch.setattr(experiment, 'accepted_context', mutate)
    with pytest.raises(ValueError, match='changed during registration'):
        experiment.register_component_ablation(root)
    assert not (root / 'artifacts/experiments').exists()


@pytest.mark.parametrize('relative', ['history-input.json', 'protocol.json', 'registration.json'])
def test_exact_frozen_bytes_detect_tampering(workspace, relative):
    spec, directory, _ = registered(workspace)
    path = directory / relative
    path.write_bytes(path.read_bytes() + (b'\n' if relative != 'registration.json' else b'garbage'))
    with pytest.raises(ValueError):
        experiment.load_registration(workspace[0], spec['sha256'])


@pytest.mark.parametrize('count', [1, 25])
def test_exact_run_declaration_is_separate_from_registration_and_matches_bounds(workspace, count):
    spec, _, _ = registered(workspace)
    value, path = authorization(workspace[0], spec, count=count)
    actual = experiment.verify_run_authorization(workspace[0], spec, path, requested_cases=count)
    assert actual == value and actual is not value
    assert actual['max_provider_calls_total'] == count * 25
    assert not actual['gold_scoring_authorized']


@pytest.mark.parametrize('field, value', [
    ('registration_sha256', '0' * 64), ('code_fingerprint_sha256', '0' * 64),
    ('protocol_sha256', '0' * 64), ('case_ids', []), ('provider', 'typesafe'),
    ('model', 'openai/gpt-oss-20b'), ('reasoning_effort', 'high'),
    ('max_output_tokens', 4096), ('max_output_tokens', True),
    ('max_provider_calls_per_arm', 6), ('max_provider_calls_total', 625),
    ('authorized_case_count', 2), ('phase', 'registered_matrix'),
    ('execution_authorized', False), ('gold_scoring_authorized', 1),
    ('holdout_access_authorized', True), ('live_access_authorized', True),
    ('new_freeze_authorized', True), ('jev_spending_authorized', True),
    ('bulk_annotation_restart_authorized', True), ('author_request', ''),
    ('date', 'not-a-date'), ('schema', 'old_policy_pilot_authorization'),
])
def test_unmatched_or_expanded_authorization_never_admits_run(workspace, field, value):
    spec, _, _ = registered(workspace)
    decision, path = authorization(workspace[0], spec)
    decision[field] = value; write(path, decision)
    with pytest.raises(ValueError):
        experiment.verify_run_authorization(workspace[0], spec, path, requested_cases=1)


def test_pilot_declaration_cannot_admit_broader_matrix(workspace):
    spec, _, _ = registered(workspace)
    _, path = authorization(workspace[0], spec)
    with pytest.raises(ValueError):
        experiment.verify_run_authorization(workspace[0], spec, path, requested_cases=2)


def test_old_or_extra_fields_are_not_inferred_as_new_authority(workspace):
    spec, _, _ = registered(workspace)
    decision, path = authorization(workspace[0], spec)
    decision['request_attachment_sha256'] = '0' * 64; write(path, decision)
    with pytest.raises(ValueError, match='new exact'):
        experiment.verify_run_authorization(workspace[0], spec, path, requested_cases=1)


def test_execution_policies_are_sealed_fixed_recipes_and_do_not_grant_authority(workspace):
    spec, _, _ = registered(workspace)
    values = [experiment.execution_policy(spec, arm) for arm in experiment.ARMS]
    assert values[0]['top_k'] == 12 and values[0]['graph_expand'] == 0
    assert all(row['top_k'] == 8 and row['graph_expand'] == 4 for row in values[1:])
    assert values[3]['independent_critic'] is True and values[3]['counter_search'] is False
    assert values[4]['counter_search'] is True
    assert all(row['registration_sha256'] == spec['sha256'] for row in values)
    with pytest.raises(ValueError):
        experiment.execution_policy(spec, 'arbitrary')
    spec['arms'][0]['top_k'] = 1
    spec['sha256'] = digest({key: value for key, value in spec.items() if key != 'sha256'})
    with pytest.raises(ValueError):
        experiment.execution_policy(spec, experiment.ARMS[0])


def test_duplicate_json_nonfinite_and_traversal_are_rejected_without_writes(workspace):
    root = workspace[0]
    for raw in (b'{"execution_authorized":false,"execution_authorized":true}', b'{"x":NaN}'):
        path = root / 'artifacts/input.json'; path.parent.mkdir(exist_ok=True); path.write_bytes(raw)
        with pytest.raises(ValueError):
            experiment.register_component_ablation(root, history_path=path)
    with pytest.raises(ValueError, match='Parent path traversal'):
        experiment.register_component_ablation(root, history_path='artifacts/../input.json')
    with pytest.raises(ValueError, match='ignored artifacts'):
        experiment.register_component_ablation(root, history_path='configs/prices.json')


def test_missing_or_small_catalog_does_not_replace_cases(workspace):
    workspace[1]['cases'][:] = workspace[1]['cases'][:24]
    with pytest.raises(ValueError, match='25 accepted original'):
        experiment.register_component_ablation(workspace[0])


def test_linked_source_tree_is_rejected_instead_of_followed(workspace):
    root = workspace[0]
    target = root / 'outside'; target.mkdir(); (target / 'other.py').write_text('x=1\n')
    link = root / 'src/issueintel/linked'
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip('Symlink creation unavailable on this platform')
    with pytest.raises(ValueError, match='links or reparse'):
        experiment.register_component_ablation(root)


@pytest.mark.skipif(os.name != 'nt', reason='Windows junction guard')
def test_actual_windows_junction_is_not_followed_by_source_or_history_reader(tmp_path):
    (tmp_path / 'artifacts').mkdir()
    target = tmp_path / 'shared'; target.mkdir()
    (target / 'canary.json').write_text('do not read or change', encoding='utf-8')
    junction = tmp_path / 'artifacts/junction'
    subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command',
        'New-Item -ItemType Junction -Path $env:ISSUEINTEL_TEST_JUNCTION -Value $env:ISSUEINTEL_TEST_JUNCTION_TARGET | Out-Null'],
        env={**os.environ, 'ISSUEINTEL_TEST_JUNCTION': str(junction),
             'ISSUEINTEL_TEST_JUNCTION_TARGET': str(target)}, capture_output=True, check=True)
    try:
        with pytest.raises(ValueError, match='links or reparse'):
            experiment._read_file(tmp_path, junction / 'canary.json', private=True)
        assert (target / 'canary.json').read_text(encoding='utf-8') == 'do not read or change'
    finally:
        assert junction.absolute().is_relative_to(tmp_path.absolute())
        os.rmdir(junction)  # Remove only this named junction, never its target.
