"""Synthetic accounting replay: ownership, incomplete inventories and tampering."""
from copy import deepcopy

import pytest

from issueintel.investigation import investigation_usage as accounting
from issueintel.runtime.io import digest
from issueintel.runtime.typesafe import INPUT_BOUND, MICRO_BOUND, MODEL, PRICE_SOURCE


def _id(index):
    return f'{index:024x}'


def _reservation(index):
    return f'{index:032x}'


def _seal(value):
    value['sha256'] = digest({key: item for key, item in value.items() if key != 'sha256'})
    return value


def _groq(index, *, status='settled', micro=101, stage='generation'):
    retained = status in {'reserved', 'failed_bound', 'missing_usage_bound'}
    return {'reservation_id': _reservation(index), 'created_at': '2026-10-22T04:00:00+00:00',
            'model': 'openai/gpt-oss-120b', 'input_bound': 100, 'output_bound': 50,
            'tokens_charged_or_reserved': 150 if retained else 24,
            'micro_usd_charged_or_reserved': micro, 'status': status, 'price_date': '2026-10-21',
            'observed_input_tokens': None if retained else 20,
            'observed_output_tokens': None if retained else 4, 'stage': stage}


def _typesafe(index, owner, *, status='settled'):
    retained = status in {'reserved', 'failed_bound', 'missing_usage_bound'}
    incoming = None if retained else (65000 if status == 'breach' else 1000)
    row = {'schema': 'typesafe_decision_receipt_v1', 'provider': 'typesafe',
           'reservation_id': _reservation(index), 'created_at': '2026-10-22T04:00:00+00:00',
           'model': MODEL, 'binding': {'job_id': owner, 'decision_index': 1,
               'query_sha256': '1' * 64, 'snapshot_sha256': '2' * 64, 'commit_sha': '3' * 40,
               'annotation_sha256': '4' * 64, 'evidence_sha256': '5' * 64},
           'request_sha256': '6' * 64,
           'response_sha256': None if status in {'reserved', 'failed_bound'} else '7' * 64,
           'status': status, 'input_bound': INPUT_BOUND, 'input_tokens': incoming,
           'output_tokens': None if retained else 100,
           'micro_usd_charged_or_reserved': MICRO_BOUND if retained else (2730 if status == 'breach' else 42),
           'price_date': '2026-10-21', 'price_source': PRICE_SOURCE, 'currency': 'USD',
           'is_provider_invoice': False}
    return _seal(row)


def _capture(receipts=(), *, applicable=None, tracking=True, attempts='infer'):
    applicable = bool(receipts) if applicable is None else applicable
    if attempts == 'infer':
        attempts = len({row.get('reservation_id') for row in receipts})
    return {'applicable': applicable, 'tracking_available': tracking, 'attempted_calls': attempts,
            'receipts': [{'receipt_sha256': digest(row), 'receipt': deepcopy(row)} for row in receipts]}


def _node(index, *, mode='github', receipts=(), typesafe=(), provider=None, pack=True,
          status='completed', tracking=True, attempts='infer'):
    provider = provider or ('groq' if receipts or attempts is None or not tracking else 'none')
    return {'job_id': _id(index), 'mode': mode, 'job_status': status, 'provider': provider,
            'request_sha256': digest({'request': index}), 'job_sha256': digest({'job': index}),
            'pack_sha256': digest({'pack': index}) if pack else None,
            'providers': {'groq': _capture(receipts, applicable=provider == 'groq', tracking=tracking, attempts=attempts),
                          'typesafe': _capture(typesafe)}}


def _edge(child, parent):
    return {'child_job_id': child['job_id'], 'parent_job_id': parent['job_id'], 'kind': child['mode'],
            'proof_sha256': digest({'edge': child['job_id']}), 'source_pack_sha256': parent['pack_sha256']}


def _snapshot(nodes=None, edges=None, selected=1, gaps=None):
    return _seal({'schema': accounting.SNAPSHOT_SCHEMA, 'selected_job_id': _id(selected),
                  'nodes': nodes or [_node(1)], 'edges': edges or [], 'gaps': gaps or []})


def _summary(bundle, provider='groq', scope='lineage'):
    return bundle[scope]['providers'][provider]


def test_offline_zero_is_explicitly_known_without_calling_any_dependency(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('No ledger, provider, configuration or Git IO allowed')
    monkeypatch.setattr('builtins.open', forbidden)
    bundle = accounting.build(_snapshot())
    assert bundle['current']['complete_micro_usd_charged_or_reserved'] == 0
    assert bundle['lineage']['all_usage_settled'] is True
    assert bundle['source']['snapshot_sha256'] == bundle['snapshot']['sha256']
    assert bundle['source']['selected_pack_sha256'] == bundle['owners'][0]['pack_sha256']
    assert bundle['captured_receipts_only'] is True
    assert bundle['is_provider_invoice'] is False
    assert bundle['absence_of_uncaptured_calls_verified'] is False
    assert accounting.validate(bundle) is bundle


def test_current_and_chain_include_generation_verifier_questions_plans_and_followup():
    root = _node(1, receipts=[_groq(1, micro=101), _groq(2, micro=102, stage='verification')],
                 typesafe=[_typesafe(1, _id(1))])
    child = _node(2, mode='followup', receipts=[_groq(3, micro=103), _groq(4, micro=104)])
    questions = _node(3, mode='adaptive_questions', receipts=[_groq(5, micro=105)])
    plan = _node(4, mode='check_plan', receipts=[_groq(6, micro=106)])
    claims = _node(5, mode='claim_review', receipts=[_groq(7, micro=107)])
    pair = _node(6, mode='issue_relation', receipts=[_groq(8, micro=108)])
    nodes = [child, root, questions, plan, claims, pair]
    edges = [_edge(child, root), _edge(questions, root), _edge(plan, child), _edge(claims, child), _edge(pair, root)]
    bundle = accounting.build(_snapshot(nodes, edges, selected=2))
    assert bundle['current']['job_ids'] == [_id(2)]
    assert bundle['current']['complete_micro_usd_charged_or_reserved'] == 207
    assert _summary(bundle)['known_micro_usd_charged_or_reserved'] == sum(range(101, 109))
    assert _summary(bundle, 'typesafe')['known_micro_usd_charged_or_reserved'] == 42
    assert bundle['lineage']['complete_micro_usd_charged_or_reserved'] == sum(range(101, 109)) + 42
    assert bundle['lineage']['inventory_complete'] is True
    assert bundle['lineage']['all_usage_settled'] is True
    # Same reservation ID in two providers is two separate reservations.
    assert _summary(bundle)['reservations'] == 8
    assert _summary(bundle, 'typesafe')['reservations'] == 1
    assert len(bundle['owners']) == 6
    assert accounting.validate(bundle)


def test_finalization_zero_current_retains_parent_cost_without_copying_receipt():
    source = _node(1, receipts=[_groq(1)], status='failed', pack=False)
    final = _node(2, mode='finalization')
    bundle = accounting.build(_snapshot([final, source], [_edge(final, source)], selected=2))
    assert bundle['current']['complete_micro_usd_charged_or_reserved'] == 0
    assert bundle['lineage']['complete_micro_usd_charged_or_reserved'] == 101
    assert _summary(bundle)['owner_job_ids'] == [_id(2), _id(1)]
    assert bundle['snapshot']['nodes'][0]['providers']['groq']['receipts'] == []


@pytest.mark.parametrize('provider', accounting.PROVIDERS)
@pytest.mark.parametrize('status', ['reserved', 'failed_bound', 'missing_usage_bound'])
def test_retained_bounds_have_complete_inventory_but_remain_unsettled(provider, status):
    receipts = [_groq(1, status=status)] if provider == 'groq' else []
    typesafe = [_typesafe(1, _id(1), status=status)] if provider == 'typesafe' else []
    bundle = accounting.build(_snapshot([_node(1, receipts=receipts, typesafe=typesafe, status='interrupted', pack=False)]))
    summary = _summary(bundle, provider)
    assert summary['inventory_complete'] is True
    assert summary['all_usage_settled'] is False
    assert summary['retained_bounds'] == summary['unsettled_reservations'] == 1
    assert summary['complete_micro_usd_charged_or_reserved'] == (101 if provider == 'groq' else MICRO_BOUND)
    assert bundle['source']['selected_pack_sha256'] is None


@pytest.mark.parametrize('provider', accounting.PROVIDERS)
def test_identical_receipt_references_are_deduplicated(provider):
    row = _node(1)
    receipt = _groq(1) if provider == 'groq' else _typesafe(1, _id(1))
    row['providers'][provider] = _capture([receipt, deepcopy(receipt)])
    if provider == 'groq':
        row['provider'] = 'groq'
    summary = _summary(accounting.build(_snapshot([row])), provider)
    assert summary['captured_receipt_records'] == 2
    assert summary['reservations'] == 1
    assert summary['duplicate_captures'] == 1
    assert summary['inventory_complete'] is True
    assert summary['known_micro_usd_charged_or_reserved'] == (101 if provider == 'groq' else 42)


@pytest.mark.parametrize('provider', accounting.PROVIDERS)
def test_conflicting_receipt_bytes_same_owner_are_unknown_not_selected(provider):
    row = _node(1)
    receipt = _groq(1) if provider == 'groq' else _typesafe(1, _id(1))
    changed = deepcopy(receipt)
    changed['created_at'] = '2026-10-22T04:00:01+00:00'
    if provider == 'typesafe':
        _seal(changed)
    row['providers'][provider] = _capture([receipt, changed])
    if provider == 'groq':
        row['provider'] = 'groq'
    summary = _summary(accounting.build(_snapshot([row])), provider)
    assert summary['captured_receipt_records'] == 2
    assert summary['reservations'] == 0
    assert summary['conflicting_reservations'] == 1
    assert summary['known_micro_usd_charged_or_reserved'] == 0
    assert summary['complete_micro_usd_charged_or_reserved'] is None
    assert 'receipt_identity_conflict' in summary['gap_codes']


def test_cross_owner_conflict_invalidates_current_as_well_as_whole_chain():
    receipt = _groq(1)
    root = _node(1, receipts=[receipt])
    selected = _node(2, mode='followup', receipts=[receipt, _groq(2, micro=300)])
    bundle = accounting.build(_snapshot([selected, root], [_edge(selected, root)], selected=2))
    assert _summary(bundle)['known_micro_usd_charged_or_reserved'] == 300
    assert _summary(bundle, scope='current')['known_micro_usd_charged_or_reserved'] == 300
    assert _summary(bundle, scope='current')['conflicting_reservations'] == 1
    assert _summary(bundle)['complete_micro_usd_charged_or_reserved'] is None
    assert bundle['lineage']['complete_micro_usd_charged_or_reserved'] is None


def test_typesafe_binding_cannot_transfer_receipt_to_new_owner():
    root = _node(1, typesafe=[_typesafe(1, _id(1))])
    child = _node(2, mode='followup', typesafe=[_typesafe(1, _id(1))])
    bundle = accounting.build(_snapshot([child, root], [_edge(child, root)], selected=2))
    summary = _summary(bundle, 'typesafe')
    assert summary['invalid_receipts'] == 1
    assert summary['conflicting_reservations'] == 1
    assert summary['known_micro_usd_charged_or_reserved'] == 0
    assert summary['complete_micro_usd_charged_or_reserved'] is None


@pytest.mark.parametrize('tracking,attempts,codes', [
    (False, None, {'tracking_unavailable', 'attempt_count_unknown'}),
    (True, None, {'attempt_count_unknown'}),
    (True, 2, {'attempt_receipt_count_mismatch'}),
    (True, 0, {'attempt_receipt_count_mismatch'}),
])
def test_legacy_or_missing_receipts_do_not_erase_known_subtotal(tracking, attempts, codes):
    row = _node(1, receipts=[_groq(1)], tracking=tracking, attempts=attempts)
    summary = _summary(accounting.build(_snapshot([row])))
    assert summary['known_micro_usd_charged_or_reserved'] == 101
    assert summary['complete_micro_usd_charged_or_reserved'] is None
    assert summary['inventory_complete'] is False
    assert summary['all_usage_settled'] is False
    assert codes <= set(summary['gap_codes'])


def test_attempt_without_reservation_is_unknown_not_zero():
    row = _node(1, provider='groq', attempts=1, status='failed', pack=False)
    bundle = accounting.build(_snapshot([row]))
    assert bundle['current']['known_micro_usd_charged_or_reserved'] == 0
    assert bundle['current']['complete_micro_usd_charged_or_reserved'] is None
    assert _summary(bundle)['attempted_calls'] == 1


def test_missing_ancestor_gap_can_leave_current_known_and_lineage_unknown():
    row = _node(1, receipts=[_groq(1)])
    gap = {'code': 'ancestor_unavailable', 'job_id': _id(99), 'provider': None,
           'detail': 'An ancestor record was unavailable during capture.'}
    bundle = accounting.build(_snapshot([row], gaps=[gap]))
    assert bundle['current']['complete_micro_usd_charged_or_reserved'] == 101
    assert bundle['lineage']['complete_micro_usd_charged_or_reserved'] is None
    assert _summary(bundle, 'typesafe')['inventory_complete'] is False


@pytest.mark.parametrize('code', sorted(accounting.LINEAGE_ONLY_GAPS))
def test_lineage_scope_gaps_do_not_make_complete_current_receipts_unknown(code):
    row = _node(1, receipts=[_groq(1)])
    gap = {'code': code, 'job_id': _id(1), 'provider': None,
           'detail': 'A source or wider-scope accounting link could not be recovered.'}
    bundle = accounting.build(_snapshot([row], gaps=[gap]))
    assert bundle['current']['complete_micro_usd_charged_or_reserved'] == 101
    assert bundle['current']['inventory_complete'] is True
    assert bundle['current']['all_usage_settled'] is True
    assert code not in _summary(bundle, scope='current')['gap_codes']
    assert code not in bundle['owners'][0]['providers']['groq']['gap_codes']
    assert bundle['lineage']['complete_micro_usd_charged_or_reserved'] is None
    assert code in _summary(bundle)['gap_codes']
    assert code in {item['code'] for item in bundle['gaps']}


@pytest.mark.parametrize('code', ['attempt_inventory_invalid', 'receipt_inventory_invalid',
                                 'receipt_public_fields_invalid', 'receipt_owner_mismatch'])
def test_owner_local_capture_gaps_still_invalidate_current_inventory(code):
    gap = {'code': code, 'job_id': _id(1), 'provider': 'groq',
           'detail': 'Own captured accounting metadata could not be validated.'}
    bundle = accounting.build(_snapshot([_node(1, receipts=[_groq(1)])], gaps=[gap]))
    assert bundle['current']['known_micro_usd_charged_or_reserved'] == 101
    assert bundle['current']['complete_micro_usd_charged_or_reserved'] is None
    assert bundle['lineage']['complete_micro_usd_charged_or_reserved'] is None


def test_missing_parent_leaves_own_call_complete_and_chain_unknown():
    bundle = accounting.build(_snapshot([_node(1, mode='stage_resume', receipts=[_groq(1)])]))
    assert bundle['current']['complete_micro_usd_charged_or_reserved'] == 101
    assert bundle['lineage']['complete_micro_usd_charged_or_reserved'] is None
    assert 'missing_parent_binding' in {item['code'] for item in bundle['gaps']}


@pytest.mark.parametrize('mode', sorted(accounting.ANCESTRY_MODES | accounting.ARTIFACT_MODES))
def test_mode_requiring_parent_cannot_become_zero_cost_root_by_omission(mode):
    bundle = accounting.build(_snapshot([_node(1, mode=mode)]))
    assert bundle['lineage']['complete_micro_usd_charged_or_reserved'] is None
    assert 'missing_parent_binding' in _summary(bundle)['gap_codes']


@pytest.mark.parametrize('provider', accounting.PROVIDERS)
def test_native_receipt_breach_keeps_captured_amount_but_unknown_complete_total(provider):
    row = _node(1, receipts=[_groq(1, status='breach')] if provider == 'groq' else [],
                typesafe=[_typesafe(1, _id(1), status='breach')] if provider == 'typesafe' else [])
    summary = _summary(accounting.build(_snapshot([row])), provider)
    assert summary['known_micro_usd_charged_or_reserved'] > 0
    assert summary['complete_micro_usd_charged_or_reserved'] is None
    assert summary['usage_anomalies'] == 1
    assert summary['all_usage_settled'] is False


@pytest.mark.parametrize('provider,field,value', [
    ('groq', 'input_bound', True), ('groq', 'output_bound', -1),
    ('groq', 'micro_usd_charged_or_reserved', False), ('groq', 'tokens_charged_or_reserved', -1),
    ('groq', 'created_at', '2026-10-22T04:00:00'), ('groq', 'price_date', '2026-10-23'),
    ('groq', 'status', 'complete'), ('groq', 'observed_input_tokens', 101),
    ('typesafe', 'input_bound', True), ('typesafe', 'sha256', 'f' * 64),
    ('typesafe', 'micro_usd_charged_or_reserved', True), ('typesafe', 'is_provider_invoice', True),
])
def test_invalid_native_public_receipt_remains_visible_and_unknown(provider, field, value):
    row = _node(1)
    receipt = _groq(1) if provider == 'groq' else _typesafe(1, _id(1))
    receipt[field] = value
    row['providers'][provider] = _capture([receipt])
    if provider == 'groq':
        row['provider'] = 'groq'
    bundle = accounting.build(_snapshot([row]))
    summary = _summary(bundle, provider)
    assert summary['invalid_receipts'] == 1
    assert summary['captured_receipt_records'] == 1
    assert summary['known_micro_usd_charged_or_reserved'] == 0
    assert summary['complete_micro_usd_charged_or_reserved'] is None
    assert 'invalid_receipt' in summary['gap_codes']
    assert accounting.validate(bundle)


def test_native_typesafe_reserved_is_valid_for_accounting_not_recovery_admission():
    row = _node(1, typesafe=[_typesafe(1, _id(1), status='reserved')])
    summary = _summary(accounting.build(_snapshot([row])), 'typesafe')
    assert summary['invalid_receipts'] == 0
    assert summary['complete_micro_usd_charged_or_reserved'] == MICRO_BOUND
    assert summary['all_usage_settled'] is False


def test_groq_legacy_receipt_without_observed_fields_is_not_reprised_or_discarded():
    receipt = _groq(1)
    del receipt['observed_input_tokens'], receipt['observed_output_tokens']
    summary = _summary(accounting.build(_snapshot([_node(1, receipts=[receipt])])))
    assert summary['complete_micro_usd_charged_or_reserved'] == 101
    assert summary['all_usage_settled'] is True


@pytest.mark.parametrize('field,value', [('micro_usd_charged_or_reserved', 1000000.25),
                                      ('prompt', 'private source text'), ('api_key', 'private-key')])
def test_noninteger_or_private_receipt_fields_never_enter_exposed_snapshot(field, value):
    receipt = _groq(1)
    receipt[field] = value
    with pytest.raises(ValueError):
        accounting.build(_snapshot([_node(1, receipts=[receipt])]))


def test_unknown_binding_fields_are_not_exported_even_on_invalid_receipts():
    receipt = _typesafe(1, _id(1))
    receipt['binding']['issue_text'] = 'private issue'
    with pytest.raises(ValueError, match='unsupported binding'):
        accounting.build(_snapshot([_node(1, typesafe=[receipt])]))


def test_scope_rejects_independent_or_sibling_investigations_even_if_connected():
    root = _node(1)
    selected = _node(2, mode='followup')
    sibling = _node(3, mode='followup', receipts=[_groq(1)])
    snapshot = _snapshot([root, selected, sibling], [_edge(selected, root), _edge(sibling, root)], selected=2)
    with pytest.raises(ValueError, match='Unselected investigation'):
        accounting.build(snapshot)
    with pytest.raises(ValueError):
        accounting.build(_snapshot([root, _node(3)]))


def test_selected_sidecar_includes_its_source_and_other_bound_artifacts():
    root = _node(1, receipts=[_groq(1)])
    selected = _node(2, mode='check_plan', receipts=[_groq(2)])
    question = _node(3, mode='adaptive_questions', receipts=[_groq(3)])
    bundle = accounting.build(_snapshot([selected, root, question], [_edge(selected, root), _edge(question, root)], selected=2))
    assert bundle['current']['complete_micro_usd_charged_or_reserved'] == 101
    assert bundle['lineage']['complete_micro_usd_charged_or_reserved'] == 303


@pytest.mark.parametrize('mutation', ['cycle', 'wrong_kind', 'wrong_pack', 'duplicate_edge', 'artifact_parent'])
def test_rehashed_edge_tampering_is_rejected(mutation):
    parent = _node(1)
    child = _node(2, mode='followup')
    snapshot = _snapshot([child, parent], [_edge(child, parent)], selected=2)
    if mutation == 'cycle':
        parent['mode'] = 'followup'
        snapshot['edges'].append(_edge(parent, child))
    elif mutation == 'wrong_kind':
        snapshot['edges'][0]['kind'] = 'finalization'
    elif mutation == 'wrong_pack':
        snapshot['edges'][0]['source_pack_sha256'] = 'f' * 64
    elif mutation == 'duplicate_edge':
        snapshot['edges'].append(deepcopy(snapshot['edges'][0]))
    else:
        parent['mode'] = 'check_plan'
    _seal(snapshot)
    with pytest.raises(ValueError):
        accounting.build(snapshot)


@pytest.mark.parametrize('target', ['total', 'bool', 'owner', 'source', 'limitations', 'invoice', 'gap', 'extra'])
def test_resealed_output_tampering_does_not_change_replay(target):
    bundle = accounting.build(_snapshot([_node(1, receipts=[_groq(1)])]))
    if target == 'total':
        bundle['lineage']['complete_micro_usd_charged_or_reserved'] = 0
    elif target == 'bool':
        bundle['current']['inventory_complete'] = 1
    elif target == 'owner':
        bundle['owners'][0]['job_id'] = _id(99)
    elif target == 'source':
        bundle['source']['selected_pack_sha256'] = 'f' * 64
    elif target == 'limitations':
        bundle['limitations'] = []
    elif target == 'invoice':
        bundle['is_provider_invoice'] = True
    elif target == 'gap':
        bundle['gaps'].append({'code': 'fake', 'job_id': _id(1), 'provider': 'groq', 'detail': 'fake'})
    else:
        bundle['extra'] = 'not allowed'
    _seal(bundle)
    with pytest.raises(ValueError, match='Changed investigation accounting'):
        accounting.validate(bundle)


def test_capture_hash_tampering_is_rejected_even_if_outer_snapshot_resealed():
    snapshot = _snapshot([_node(1, receipts=[_groq(1)])])
    snapshot['nodes'][0]['providers']['groq']['receipts'][0]['receipt']['micro_usd_charged_or_reserved'] = 999
    _seal(snapshot)
    with pytest.raises(ValueError, match='Changed captured receipt'):
        accounting.build(snapshot)


@pytest.mark.parametrize('target', ['nodes', 'edges', 'receipt', 'gaps', 'depth', 'attempt_bool'])
def test_bounded_typed_snapshot_rejects_invalid_inventory(target):
    snapshot = _snapshot()
    if target == 'nodes':
        snapshot['nodes'] *= accounting.MAX_NODES + 1
    elif target == 'edges':
        snapshot['edges'] = [{}] * (accounting.MAX_EDGES + 1)
    elif target == 'receipt':
        snapshot['nodes'][0]['providers']['groq']['receipts'] = [{}] * (accounting.MAX_RECEIPTS + 1)
    elif target == 'gaps':
        snapshot['gaps'] = [{}] * (accounting.MAX_GAPS + 1)
    elif target == 'depth':
        snapshot['gaps'] = [[[[[[[[[[[[[[0]]]]]]]]]]]]]]
    else:
        snapshot['nodes'][0]['providers']['groq']['attempted_calls'] = False
    _seal(snapshot)
    with pytest.raises(ValueError):
        accounting.build(snapshot)


def test_groq_provider_cannot_be_resealed_as_not_applicable():
    node = _node(1, provider='groq', attempts=0)
    node['providers']['groq']['applicable'] = False
    with pytest.raises(ValueError, match='cannot declare'):
        accounting.build(_snapshot([node]))


def test_build_does_not_mutate_captured_sources_and_output_is_detached():
    snapshot = _snapshot([_node(1, receipts=[_groq(1)])])
    original = deepcopy(snapshot)
    bundle = accounting.build(snapshot)
    assert snapshot == original
    bundle['snapshot']['nodes'][0]['providers']['groq']['receipts'][0]['receipt']['status'] = 'reserved'
    assert snapshot == original


def _included_run(node, parent=None):
    return {'job_id': node['job_id'], 'mode': node['mode'], 'status': node['job_status'],
            'provider': node['provider'], 'request_sha256': node['request_sha256'],
            'binding': {'pack_sha256': node['pack_sha256']} if node['pack_sha256'] is not None else None,
            'parent_job_id': parent}


@pytest.mark.parametrize('field', ['mode', 'job_status', 'provider', 'request_sha256', 'pack_sha256'])
def test_included_ancestor_metadata_cannot_disagree_with_resealed_accounting_capture(field):
    parent = _node(1, receipts=[_groq(1)])
    child = _node(2, mode='followup', receipts=[_groq(2)])
    runs = {_id(1): _included_run(parent), _id(2): _included_run(child, _id(1))}
    altered = deepcopy(parent)
    altered[field] = {'mode': 'local', 'job_status': 'failed', 'provider': 'none',
                      'request_sha256': 'e' * 64, 'pack_sha256': 'f' * 64}[field]
    bundle = accounting.build(_snapshot([child, altered], [_edge(child, altered)], selected=2))
    with pytest.raises(ValueError, match='Accounting owner differs'):
        accounting.validate_run_bindings(bundle, runs)


def test_included_child_parent_cannot_disagree_with_resealed_cost_ancestry():
    original = _node(1, receipts=[_groq(1)])
    child = _node(2, mode='followup', receipts=[_groq(2)])
    different = _node(3, receipts=[_groq(3)])
    runs = {_id(1): _included_run(original), _id(2): _included_run(child, _id(1))}
    bundle = accounting.build(_snapshot([child, different], [_edge(child, different)], selected=2))
    with pytest.raises(ValueError, match='differs from included run ancestry'):
        accounting.validate_run_bindings(bundle, runs)


def test_crossbinding_keeps_metadata_only_failed_ancestor_and_outside_sidecar_readable():
    parent = _node(1, receipts=[_groq(1)], status='failed')
    child = _node(2, mode='finalization')
    sidecar = _node(3, mode='check_plan', receipts=[_groq(3)])
    runs = {_id(1): _included_run(parent), _id(2): _included_run(child, _id(1))}
    # A failed run may keep original raw pack bytes without a completed report.
    runs[_id(1)]['binding'] = None
    bundle = accounting.build(_snapshot([child, parent, sidecar], [_edge(child, parent), _edge(sidecar, parent)], selected=2))
    assert accounting.validate_run_bindings(bundle, runs) is bundle


def test_missing_captured_parent_edge_can_be_shared_as_explicit_unknown():
    child = _node(2, mode='stage_resume', receipts=[_groq(2)])
    bundle = accounting.build(_snapshot([child], selected=2))
    runs = {_id(2): _included_run(child, _id(1))}
    assert accounting.validate_run_bindings(bundle, runs) is bundle
    assert bundle['lineage']['complete_micro_usd_charged_or_reserved'] is None


def test_markdown_shows_unknown_and_retained_bounds_and_escapes_metadata():
    row = _node(1, receipts=[_groq(1, status='reserved')], attempts=None)
    gap = {'code': 'source_unavailable', 'job_id': _id(1), 'provider': 'groq',
           'detail': '<unsafe> | metadata'}
    bundle = accounting.build(_snapshot([row], gaps=[gap]))
    markdown = accounting.render_markdown(bundle)
    assert '| lineage | groq | 0.000101 | unknown | 1 | 1 | false | false |' in markdown
    assert '&lt;unsafe&gt; \\| metadata' in markdown
    assert '<unsafe>' not in markdown
    assert 'not a provider invoice' in markdown
    assert 'Unknown contributors are not zero' in markdown
    assert bundle['sha256'] in markdown
