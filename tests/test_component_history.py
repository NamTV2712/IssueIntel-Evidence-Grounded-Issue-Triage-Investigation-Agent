"""Bounded cached original Main Dev opening selection, using synthetic bytes."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pytest

from issueintel.benchmarks import component_history as history
from issueintel.runtime.io import digest
from test_component_ablation import source, historical, write


def reference(root, path, value):
    write(path, value)
    return {'path': path.relative_to(root).as_posix(), 'sha256': sha256(path.read_bytes()).hexdigest()}


@pytest.fixture
def cache(tmp_path, monkeypatch):
    root = tmp_path
    cases = {f'verified:synthetic__case-{i:03d}': source(root, f'verified:synthetic__case-{i:03d}', 'synthetic/a')[0]
             for i in range(400)}
    contexts = {cid: source(root, cid, case['repository']) for cid, case in cases.items()}
    members = {}
    proofs = {}
    for i, cid in enumerate(list(cases)[:30]):
        proof = {'issue_receipts': [historical(number=i + 1)]}
        path = root / f'data/development/synthetic/proof-{i}.json'
        proof_ref = reference(root, path, proof)
        record = {'version': history.VERSION, 'namespace': history.NAMESPACE,
            'pool_sha256': 'e' * 64, 'case_id': cid, 'case_sha256': digest(cases[cid]),
            'proof': proof_ref, 'accepted': True, 'llm_calls': 0, 'gold_used': False,
            'human_rationale_used': False, 'evaluation_authorized': False}
        record_ref = reference(root, root / f'data/development/synthetic/record-{i}.json', record)
        members[cid] = {'case_id': cid, 'origin': 'frozen', 'receipt': record_ref}
        proofs[cid] = proof
    catalog = {'original_total': 400, 'accepted': len(members), 'evaluation_authorized': False,
        'checkpoint_sha256': 'd' * 64, 'cases': [dict(case_id=cid, repository=cases[cid]['repository']) for cid in members],
        '_members': members, '_candidates': {cid: {'case': case} for cid, case in cases.items()},
        '_parent': {'pool_sha256': 'e' * 64}}
    calls = []
    monkeypatch.setattr(history, 'development_catalog', lambda root: deepcopy(catalog))
    def accepted(root, cid):
        calls.append(cid)
        return contexts[cid]
    monkeypatch.setattr(history, 'accepted_context', accepted)
    monkeypatch.setattr('issueintel.runtime.config.RuntimeConfig.load', lambda *a, **k: pytest.fail('runtime config loaded'))
    monkeypatch.setattr('socket.create_connection', lambda *a, **k: pytest.fail('network used'))
    monkeypatch.setattr('subprocess.Popen', lambda *a, **k: pytest.fail('external process started'))
    return root, catalog, contexts, proofs, calls


def replace_proof(cache, cid, proof):
    root, catalog, _, proofs, _ = cache
    member = catalog['_members'][cid]
    record_path = root / member['receipt']['path']
    record = json.loads(record_path.read_bytes())
    record['proof'] = reference(root, root / record['proof']['path'], proof)
    member['receipt'] = reference(root, record_path, record)
    proofs[cid] = proof


def prepared(cache, name='history.json'):
    root = cache[0]
    result = history.prepare_component_history(root, root / 'artifacts' / name)
    mapping = json.loads(Path(result['history_path']).read_bytes())
    audit = json.loads(Path(result['audit_path']).read_bytes())
    return result, mapping, audit


def test_preparer_freezes_existing_sources_for_exact_25_and_never_loads_env_or_gold(cache, monkeypatch):
    root = cache[0]
    (root / '.env').write_text('PRIVATE_CANARY=not_read\n')
    restricted = root / 'data/restricted'; restricted.mkdir()
    (restricted / 'gold.json').write_text('{"patch":"not read"}')
    original = Path.open
    def guard(path, *args, **kwargs):
        if path.name == '.env' or restricted in path.parents:
            pytest.fail('Environment or restricted gold opened')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', guard)
    result, mapping, audit = prepared(cache)
    expected = history.experiment._selected_rows(cache[1])
    assert set(mapping) == {row['case_id'] for row in expected}
    assert cache[4] == [row['case_id'] for row in expected] and len(cache[4]) == 25
    assert result['history_covered_cases'] == 25 and result['history_contrast_informative']
    assert all(len(items) == 20 for items in mapping.values())
    assert all(row['included_sources'] == 4 for row in audit['targets'])
    assert audit['scan']['accepted_members'] == audit['scan']['donor_documents_inspected'] == 30
    assert audit['scan']['receipt_occurrences_inspected'] == 30
    assert audit['sha256'] == digest({key: value for key, value in audit.items() if key != 'sha256'})
    assert audit['history_sha256'] == sha256(Path(result['history_path']).read_bytes()).hexdigest()
    assert 'Prior observed symptom' not in json.dumps(audit)
    assert audit['gold_read'] is audit['holdout_or_live_access'] is audit['quality_claim'] is False
    assert not audit['absence_or_exhaustive_search_claim'] and audit['network_calls'] == 0


def test_repeat_source_preparation_is_deterministic_and_never_overwrites(cache):
    first, mapping, _ = prepared(cache)
    second, other, _ = prepared(cache, 'other.json')
    assert mapping == other and first['history_sha256'] == second['history_sha256']
    original = Path(first['history_path']).read_bytes()
    with pytest.raises(FileExistsError):
        history.prepare_component_history(cache[0], first['history_path'])
    assert Path(first['history_path']).read_bytes() == original


def test_native_gate_rejects_later_equal_missing_opening_and_target_sources(cache):
    ids = list(cache[3])
    for index, number in enumerate((99, 2, 3, 4)):
        item = historical(number=number)
        if index in (1, 2):
            stamp = '2020-01-03T00:00:00Z' if index == 1 else '2020-01-04T00:00:00Z'
            item['payload'].update(created_at=stamp, updated_at=stamp)
            item['body_provenance']['available_at'] = stamp
            event = item['body_provenance']['payload']; event['created_at'] = stamp
            event['payload']['issue'] = deepcopy(item['payload'])
            item['body_provenance']['source_url'] = f'https://data.gharchive.org/{stamp[:10]}-0.json.gz'
        if index == 3:
            item.pop('body_provenance')
        replace_proof(cache, ids[index], {'issue_receipts': [item]})
    _, mapping, audit = prepared(cache)
    for items in mapping.values():
        assert not {item['payload']['number'] for item in items} & {99, 2, 3, 4}
    reasons = audit['targets'][0]['rejected_or_unselected_reasons']
    assert reasons['target_issue_is_not_prior_history'] == 1
    assert reasons['source_not_strictly_before_cutoff'] == 2
    assert reasons['independent_opening_event_missing'] == 1


def test_original_receipts_and_current_receipts_are_both_scanned_but_hashes_are_deduplicated(cache):
    cid = list(cache[3])[0]
    receipt = cache[3][cid]['issue_receipts'][0]
    replace_proof(cache, cid, {'issue_receipts': [receipt], 'current_issue_receipts': [deepcopy(receipt)]})
    _, _, audit = prepared(cache)
    assert audit['scan']['receipt_occurrences_inspected'] == 31
    assert audit['scan']['duplicate_receipts'] == 1
    assert audit['scan']['unique_github_issue_candidates'] == 30


def test_different_receipts_for_same_issue_do_not_duplicate_selected_identity(cache):
    ids = list(cache[3])
    receipt = deepcopy(cache[3][ids[0]]['issue_receipts'][0])
    receipt['fetched_at'] = '2026-10-13T00:00:00Z'
    replace_proof(cache, ids[1], {'issue_receipts': [receipt]})
    _, mapping, audit = prepared(cache)
    assert all(len({item['source_url'] for item in items}) == len(items) for items in mapping.values())
    assert audit['targets'][0]['rejected_or_unselected_reasons']['duplicate_eligible_issue_identity'] == 1


def test_sources_are_ordered_by_latest_qualifying_emission_then_url_and_hash(cache):
    cid = list(cache[3])[-1]
    receipt = deepcopy(cache[3][cid]['issue_receipts'][0])
    receipt['body_provenance']['payload']['created_at'] = '2020-01-02T00:00:00Z'
    receipt['body_provenance']['source_url'] = 'https://data.gharchive.org/2020-01-02-0.json.gz'
    replace_proof(cache, cid, {'issue_receipts': [receipt]})
    _, mapping, _ = prepared(cache)
    for items in mapping.values():
        assert items[0] == receipt
        assert [item['source_url'] for item in items[1:]] == sorted(item['source_url'] for item in items[1:])


@pytest.mark.parametrize('change', [
    lambda receipt: receipt.update(patch='forbidden'),
    lambda receipt: receipt['payload'].update(human_rationale='forbidden'),
])
def test_hidden_fields_in_raw_receipts_fail_before_projection_or_publication(cache, change):
    cid = list(cache[3])[0]
    proof = deepcopy(cache[3][cid]); change(proof['issue_receipts'][0])
    replace_proof(cache, cid, proof)
    with pytest.raises(ValueError, match='Hidden'):
        history.prepare_component_history(cache[0], 'artifacts/history.json')
    assert not (cache[0] / 'artifacts/history.json').exists()


@pytest.mark.parametrize('target', ['record', 'proof'])
def test_cached_source_hash_mismatch_fails_and_never_rewrites_it(cache, target):
    root, catalog, _, _, _ = cache
    member = next(iter(catalog['_members'].values()))
    record_path = root / member['receipt']['path']
    path = record_path if target == 'record' else root / json.loads(record_path.read_bytes())['proof']['path']
    changed = path.read_bytes() + b'\n'
    path.write_bytes(changed)
    with pytest.raises(ValueError, match='hash changed'):
        history.prepare_component_history(root, 'artifacts/history.json')
    assert path.read_bytes() == changed and not (root / 'artifacts/history.json').exists()


@pytest.mark.parametrize('field,value', [('accepted', False), ('gold_used', True), ('llm_calls', 1),
    ('human_rationale_used', True), ('evaluation_authorized', True), ('case_sha256', '0' * 64)])
def test_donor_record_isolation_and_exact_source_case_are_checked(cache, field, value):
    root, catalog, _, _, _ = cache
    member = next(iter(catalog['_members'].values()))
    path = root / member['receipt']['path']
    record = json.loads(path.read_bytes()); record[field] = value
    member['receipt'] = reference(root, path, record)
    with pytest.raises(ValueError, match='isolated accepted'):
        history.prepare_component_history(root, 'artifacts/history.json')


@pytest.mark.parametrize('change', [
    lambda catalog: catalog.update(original_total=401),
    lambda catalog: catalog.update(evaluation_authorized=True),
    lambda catalog: catalog['_candidates'][next(iter(catalog['_candidates']))]['case'].update(split='main_test'),
    lambda catalog: catalog['_members'][next(iter(catalog['_members']))].update(origin='imported'),
])
def test_other_splits_expansion_and_changed_membership_are_rejected(cache, change):
    change(cache[1])
    with pytest.raises(ValueError):
        history.prepare_component_history(cache[0], 'artifacts/history.json')
    assert cache[4] == []


def test_sources_without_supported_opening_list_and_trac_stay_unavailable(cache):
    for cid in cache[3]:
        replace_proof(cache, cid, {'issue_receipts': [{'source_url': 'https://code.djangoproject.com/ticket/1', 'payload': {'ticket_number': 1}}]})
    result, mapping, audit = prepared(cache)
    assert result['history_covered_cases'] == 0 and not result['history_contrast_informative']
    assert all(value == [] for value in mapping.values())
    assert audit['scan']['unavailable_reasons']['unsupported_source_url'] == 30
    assert len(mapping) == 25 and not audit['absence_or_exhaustive_search_claim']


def test_receipt_cap_and_document_byte_cap_report_partial_without_replacing_targets(cache, monkeypatch):
    monkeypatch.setattr(history, 'MAX_RECEIPTS', 2)
    result, mapping, audit = prepared(cache)
    assert result['partial_source_scan'] and len(mapping) == 25
    assert all(len(items) == 2 for items in mapping.values())
    assert audit['scan']['receipt_occurrences_inspected'] == 2
    assert audit['scan']['unavailable_reasons']['receipt_limit_members_uninspected'] == 28
    monkeypatch.setattr(history, 'MAX_PROOF_BYTES', 1)
    result, mapping, audit = prepared(cache, 'oversized.json')
    assert result['partial_source_scan'] and all(items == [] for items in mapping.values())
    assert audit['scan']['unavailable_reasons']['proof_document_byte_limit'] == 30


def test_aggregate_scan_byte_limit_never_reads_over_budget_and_declares_unknowns(cache, monkeypatch):
    monkeypatch.setattr(history, 'MAX_SCAN_BYTES', 1)
    result, mapping, audit = prepared(cache)
    assert result['partial_source_scan'] and audit['scan']['document_bytes_read'] == 0
    assert all(items == [] for items in mapping.values())
    assert audit['scan']['unavailable_reasons']['scan_byte_limit_members_uninspected'] == 30


def test_case_byte_budget_keeps_valid_coverage_and_explains_unselected_sources(cache, monkeypatch):
    monkeypatch.setattr(history.experiment, 'MAX_JSON_BYTES', 2048)
    _, mapping, audit = prepared(cache)
    assert all(0 < len(items) < 20 for items in mapping.values())
    assert all(len(history.experiment._bytes(items)) <= 2048 for items in mapping.values())
    assert all(row['rejected_or_unselected_reasons']['case_receipt_byte_limit'] > 0 for row in audit['targets'])


def test_oversized_individual_receipt_is_explicit_unavailable_without_removing_target(cache):
    cid = list(cache[3])[0]
    large = historical(number=1, body='x' * (history.MAX_SOURCE_BYTES // 2 + 1))
    replace_proof(cache, cid, {'issue_receipts': [large]})
    result, mapping, audit = prepared(cache)
    assert result['partial_source_scan'] and len(mapping) == 25
    assert audit['scan']['unavailable_reasons']['receipt_byte_limit'] == 1
    assert all(large not in items for items in mapping.values())


def test_no_source_lists_are_preserved_as_zero_coverage_with_no_absence_claim(cache):
    for cid in cache[3]:
        replace_proof(cache, cid, {'event_schema_version': 'unsupported_for_this_preparer'})
    result, mapping, audit = prepared(cache)
    assert not result['history_contrast_informative'] and all(items == [] for items in mapping.values())
    assert audit['scan']['unavailable_reasons']['no_supported_issue_receipt_list'] == 30
    assert audit['absence_or_exhaustive_search_claim'] is False


def test_recovery_donor_self_seal_and_parent_are_checked(cache):
    root, catalog, _, _, _ = cache
    member = next(iter(catalog['_members'].values()))
    path = root / member['receipt']['path']
    record = json.loads(path.read_bytes())
    record['parent_cohort_sha256'] = history.PARENT
    record['receipt_sha256'] = digest(record)
    member['origin'] = 'recovery'; member['receipt'] = reference(root, path, record)
    result, _, _ = prepared(cache)
    assert result['history_covered_cases'] == 25
    record['receipt_sha256'] = '0' * 64
    member['receipt'] = reference(root, path, record)
    with pytest.raises(ValueError, match='Recovered history donor identity'):
        history.prepare_component_history(root, 'artifacts/other.json')


def test_target_proof_must_still_bind_exact_snapshot_and_query(cache):
    cid = list(cache[2])[0]
    case, snapshot, query = cache[2][cid]
    cache[2][cid] = case, snapshot, dict(query, strict_dev_proof_sha256='0' * 64)
    with pytest.raises(ValueError, match='exact accepted original strict'):
        history.prepare_component_history(cache[0], 'artifacts/history.json')


def test_source_changes_during_target_replay_prevent_publication(cache, monkeypatch):
    root, catalog, contexts, _, _ = cache
    path = root / next(iter(catalog['_members'].values()))['receipt']['path']
    def mutate(root, cid):
        path.write_bytes(path.read_bytes() + b'\n')
        return contexts[cid]
    monkeypatch.setattr(history, 'accepted_context', mutate)
    with pytest.raises(ValueError):
        history.prepare_component_history(root, 'artifacts/history.json')
    assert not (root / 'artifacts/history.json').exists()


def test_history_output_stays_private_and_existing_audit_is_not_overwritten(cache):
    root = cache[0]
    for output in ('docs/history.json', 'artifacts/../history.json', 'artifacts/history.md'):
        with pytest.raises(ValueError):
            history.prepare_component_history(root, output)
    existing = write(root / 'artifacts/history.audit.json', {'existing': True})
    with pytest.raises(FileExistsError):
        history.prepare_component_history(root, 'artifacts/history.json')
    assert json.loads(existing.read_bytes()) == {'existing': True}
    assert not (root / 'artifacts/history.json').exists()
