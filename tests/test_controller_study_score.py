"""Synthetic descriptive controller metrics; no credentials or evaluation gold."""
from copy import deepcopy

import pytest

from issueintel.evaluation.controller_study_score import summarize_study, validate_rule_shadow
from issueintel.evaluation.controller_study import _protocol
from issueintel.investigation.pipeline import investigate
from issueintel.investigation.product_study import context_binding
from issueintel.investigation.typesafe_controller import TypesafePlannedController
from issueintel.runtime.io import digest
from issueintel.runtime.study_budget import StudyBudgetLedger
from issueintel.repository.graph import build_graph
from issueintel.repository.snapshot import build_snapshot
from test_planned_pipeline import planned_repository, PlannedModel
from test_study_budget import policy, arguments, groq_receipt, typesafe_receipt, hashed
from test_typesafe_controller import FakeClient


def seal(row):
    row.pop('sha256', None)
    row['sha256'] = digest(row)
    return row


@pytest.fixture
def native(planned_repository):
    original, path = planned_repository
    case = {**original, 'case_id': 'https://github.com/example/study/issues/7',
        'issue_url': 'https://github.com/example/study/issues/7', 'repository': 'example/study'}
    snapshot = build_snapshot(path, case['revision'])
    options = dict(snapshot=snapshot, graph=build_graph(snapshot), provider='groq', model='openai/gpt-oss-120b', top_k=12,
        independent_critic=True, max_output_tokens=4096,
        planned_workflow=True, planner_policy='bounded_planner_v2')
    rules = investigate(case, path, complete_json=PlannedModel(), **options)
    jev = investigate(case, path, complete_json=PlannedModel(),
        controller=TypesafePlannedController(FakeClient(action='stop'), 'a' * 24), **options)
    fallback = investigate(case, path, complete_json=PlannedModel(),
        controller=TypesafePlannedController(FakeClient(error='disabled'), 'b' * 24), **options)
    return {'rules': rules, 'jev': jev, 'fallback': fallback}


@pytest.fixture
def study(tmp_path, native):
    context = context_binding(native['rules']['case'], native['rules']['snapshot'], native['rules']['graph'])
    cases = [{'unit_id': f'case-{n:02}', 'context': deepcopy(context)} for n in range(1, 6)]
    budget = policy(arms=[{'arm_id': c['unit_id'] + ':' + a, 'max_attempts': 20, 'max_micro_usd': 20000}
                         for c in cases for a in ('rules', 'jev')])
    spec = seal({'schema': 'controller_study_registration_v1', 'study_id': budget['study_id'],
        'cases': cases, 'protocol': _protocol(), 'budget_policy': budget})
    return spec, StudyBudgetLedger(tmp_path / 'budget', budget)


def result(spec, arm, pack=None, *, unit='case-01', status='completed', **changes):
    value = {'schema': 'controller_study_arm_v1', 'study_id': spec['study_id'],
        'registration_sha256': spec['sha256'], 'unit_id': unit, 'arm_id': arm,
        'status': status, 'pack': deepcopy(pack),
        'failure': None if status == 'completed' else {'reason': 'synthetic_unavailable'},
        'source': next(row['context'] for row in spec['cases'] if row['unit_id'] == unit),
        'stop_reason': pack['stop_reason'] if pack else 'synthetic_unavailable',
        'stage_refs': [], 'call_ids': [], 'owned_call_ids': [],
        'pipeline_latency_seconds': 3, 'preparation_seconds': 2,
        'preparation_measurement_id': hashed(unit + ' preparation'),
        'preparation_owner_arm_id': 'rules'}
    value.update(changes)
    return seal(value)


def paid(ledger, *, stage='seed:1', request='seed', outcome='completed', rid='a' * 32,
         status='settled', arm='case-01:rules', share=True, observations=True):
    cid = ledger.reserve(**arguments(arm=arm, stage=stage, request=request))
    value = groq_receipt(rid=rid, status=status)
    if not observations and status == 'settled':
        value['observed_input_tokens'] = value['observed_output_tokens'] = None
    ledger.publish_receipt(cid, value)
    ledger.finish(cid, outcome, hashed('native ' + request) if outcome == 'completed' else None,
                  reason=None if outcome == 'completed' else 'synthetic_failure')
    if share:
        ledger.reference(cid, 'case-01:jev', stage, hashed(request))
    return cid


def cell(score, arm, unit='case-01'):
    return next(row for row in score['cells'] if row['unit_id'] == unit and row['arm_id'] == arm)


def test_fixed_selected_denominator_keeps_failed_unavailable_and_unattempted(study, native):
    spec, ledger = study
    rows = [result(spec, 'rules', native['rules']), result(spec, 'jev', native['jev']),
            result(spec, 'rules', unit='case-02', status='failed'),
            result(spec, 'jev', unit='case-03', status='unavailable')]
    score = summarize_study(spec, rows, ledger.inspect())
    assert score['schema'] == 'controller_study_scores_v1'
    assert score['selected_case_clusters'] == 5 and score['selected_arm_rows'] == 10
    assert score['arms']['rules']['status_counts'] == {'completed': 1, 'failed': 1, 'unavailable': 0, 'not_attempted': 3}
    assert score['arms']['jev']['status_counts'] == {'completed': 1, 'failed': 0, 'unavailable': 1, 'not_attempted': 3}
    assert score['arms']['rules']['completion_fraction_of_selected'] == .2
    assert score['paired_completed_subset']['case_cluster_count'] == 1
    assert score['paired_completed_subset']['availability_fraction'] == .2
    assert cell(score, 'rules', 'case-02')['metrics'] is None
    assert all(score[key] is False for key in ('semantic_truth_computed', 'action_accuracy_computed', 'developer_benefit_computed'))
    assert 'native_pack' not in cell(score, 'rules')
    assert cell(score, 'rules')['native_pack_sha256'] == digest(native['rules'])
    assert score['sha256'] == digest({k: v for k, v in score.items() if k != 'sha256'})


def test_selected_stop_is_disagreement_and_fallback_is_not_a_jev_selection(study, native):
    spec, ledger = study
    selected = validate_rule_shadow(native['jev'])
    assert len(selected) == 1
    assert selected[0]['actual_action'] == selected[0]['jev_selected_action'] == 'stop'
    assert selected[0]['rule_shadow_action'] == 'retrieve' and not selected[0]['agrees_with_rules']
    assert selected[0]['state']['evidence_count'] == sum(r['kind'] == 'code' for r in native['jev']['evidence'])
    assert selected[0]['state']['evidence_count'] < len(native['jev']['evidence'])
    fallback = validate_rule_shadow(native['fallback'])
    assert all(row['fallback'] and row['jev_selected_action'] is None for row in fallback)
    assert all(row['agrees_with_rules'] and row['fallback_reason'] == 'disabled' for row in fallback)
    assert [row['state']['rounds_remaining'] for row in fallback] == [3, 2, 1, 0]
    assert all(row['state']['missing_information'] == native['fallback']['intake']['missing_information'] for row in fallback)
    score = summarize_study(spec, [result(spec, 'jev', native['fallback'])], ledger.inspect())
    metrics = cell(score, 'jev')['metrics']
    assert metrics['jev_selected_decisions'] == 0 and metrics['fallback_decisions'] == 4
    assert metrics['fallback_rule_agreement_count'] == 4
    stopped = summarize_study(spec, [result(spec, 'jev', native['jev'])], ledger.inspect())
    assert cell(stopped, 'jev')['metrics']['final_action'] == 'stop'
    assert cell(stopped, 'jev')['metrics']['critic_kind'] == 'structural'
    assert stopped['arms']['jev']['critic_coverage'] == {'not_run': 0, 'structural': 1, 'llm_separate_pass': 0}


def test_retrieval_gap_and_critic_counts_describe_scope_not_truth(study, native):
    spec, ledger = study
    score = summarize_study(spec, [result(spec, 'rules', native['rules'])], ledger.inspect())
    metrics = cell(score, 'rules')['metrics']
    assert metrics['retrieval_query_count'] == metrics['executed_query_count'] + metrics['unavailable_query_count']
    assert metrics['zero_yield_retrieval_rounds'] > 0
    assert metrics['zero_yield_executed_query_count'] > 0
    assert metrics['unavailable_query_count'] > 0
    assert sum(metrics['query_gap_codes'].values()) == metrics['retrieval_query_count']
    assert metrics['critic_kind'] == 'llm_separate_pass' and metrics['critic_review_count'] == 3
    assert metrics['critic_assessments'] == {'uncertain': 3}
    assert metrics['structural_citation_integrity']['status'] == 'valid'
    assert metrics['hypotheses_without_counter_citation'] == 3


def test_shared_failed_repair_counts_once_physically_and_twice_logically(study):
    spec, ledger = study
    failed = paid(ledger, stage='seed:1', request='malformed', outcome='failed', status='failed_bound')
    repaired = paid(ledger, stage='seed:2', request='repair', rid='b' * 32, observations=False)
    snapshot = ledger.inspect()
    score = summarize_study(spec, [], snapshot,
        {failed: {'call_id': failed, 'latency_seconds': .25, 'request_sha256': hashed('malformed')}})
    usage = score['usage']
    assert usage['physical_unique']['attempts'] == 2
    assert usage['physical_unique']['micro_usd_charged_or_reserved'] == 231
    assert usage['physical_unique']['receipt_status_counts'] == {'failed_bound': 1, 'settled': 1}
    assert usage['physical_unique']['outcome_counts'] == {'completed': 1, 'failed': 1}
    assert all(usage['logical_by_arm'][a]['micro_usd_charged_or_reserved'] == 231 for a in ('rules', 'jev'))
    assert usage['logical_costs_are_additive_across_arms'] is False
    assert usage['physical_unique']['observed_input_tokens'] == 0
    assert usage['physical_unique']['groq_tokens_charged_or_reserved'] == 1210
    assert usage['physical_unique']['bounded_or_unreceipted_calls'] == 1
    assert usage['physical_unique']['bounded_or_unreceipted_micro_usd'] == 210
    assert usage['physical_unique']['unknown_input_token_calls'] == 2
    assert usage['physical_unique']['unknown_output_token_calls'] == 2
    assert usage['physical_unique']['known_latency_seconds_sum'] == .25
    assert usage['physical_unique']['unknown_latency_calls'] == 1
    assert usage['physical_unique']['latency_seconds_sum'] is None
    assert usage['by_provider']['groq']['attempts'] == 2
    assert usage['by_provider']['typesafe']['attempts'] == 0
    assert set(cell(score, 'rules')['unreported_owned_call_ids']) == {failed, repaired}
    assert cell(score, 'jev')['owned_call_ids'] == []
    assert set(cell(score, 'jev')['referenced_call_ids']) == {failed, repaired}


def test_unreceipted_unknown_preserves_full_bound_and_unknown_timing(study):
    spec, ledger = study
    cid = ledger.reserve(**arguments())
    ledger.close_unknown(cid, 'synthetic_crash')
    score = summarize_study(spec, [], ledger.inspect())
    usage = score['usage']['physical_unique']
    assert usage['micro_usd_charged_or_reserved'] == 210
    assert usage['missing_receipt_count'] == 1 and usage['outcome_counts'] == {'unknown': 1}
    assert usage['unknown_latency_calls'] == 1 and usage['latency_seconds_sum'] is None
    assert cell(score, 'rules')['unreported_owned_call_ids'] == [cid]


def test_failed_typesafe_cost_is_provider_separate_and_not_silently_waived(study):
    spec, ledger = study
    paid(ledger, share=False)
    cid = ledger.reserve(**arguments(arm='case-01:jev', stage='decision:1', request='jev', provider='typesafe'))
    ledger.publish_receipt(cid, typesafe_receipt(status='failed_bound', request='jev'))
    ledger.finish(cid, 'failed', reason='synthetic_transport_failure')
    score = summarize_study(spec, [], ledger.inspect())
    usage = score['usage']
    assert usage['physical_unique']['micro_usd_charged_or_reserved'] == 2709
    assert usage['by_provider']['typesafe']['micro_usd_charged_or_reserved'] == 2688
    assert usage['by_provider']['typesafe']['receipt_count'] == 1
    assert usage['by_provider']['typesafe']['bounded_or_unreceipted_calls'] == 1
    assert usage['by_provider']['typesafe']['outcome_counts'] == {'failed': 1}
    assert usage['by_provider']['groq']['micro_usd_charged_or_reserved'] == 21
    assert usage['logical_by_arm']['rules']['micro_usd_charged_or_reserved'] == 21
    assert usage['logical_by_arm']['jev']['micro_usd_charged_or_reserved'] == 2688


def test_preparation_references_deduplicate_physical_time_and_active_latency_stays_separate(study, native):
    spec, ledger = study
    rows = [result(spec, 'rules', native['rules']), result(spec, 'jev', native['jev'])]
    before = deepcopy(rows)
    score = summarize_study(spec, rows, ledger.inspect())
    assert rows == before
    assert score['preparation_timing']['unique_measurements'] == 1
    assert score['preparation_timing']['known_seconds_sum'] == 2
    assert score['preparation_timing']['arm_rows_without_measurement'] == 8
    assert score['arms']['rules']['pipeline_active_timing'] == {'known_seconds_sum': 3, 'unknown_arm_rows': 4}
    rows[1]['preparation_seconds'] = 3
    seal(rows[1])
    with pytest.raises(ValueError, match='preparation timing'):
        summarize_study(spec, rows, ledger.inspect())


@pytest.mark.parametrize('mutation', ['registration_seal', 'result_seal', 'bool_time', 'nan_time',
    'bool_budget_count', 'bool_budget_total', 'wrong_source', 'duplicate_result', 'wrong_controller',
    'invalid_pack_flag', 'fake_complete', 'wrong_study'])
def test_malformed_and_tampered_views_are_rejected(study, native, mutation):
    spec, ledger = study
    spec = deepcopy(spec); rows = [result(spec, 'rules', native['rules'])]; snapshot = ledger.inspect()
    if mutation == 'registration_seal':spec['study_id'] = 'f' * 64
    elif mutation == 'result_seal':rows[0]['pipeline_latency_seconds'] = 5
    elif mutation == 'bool_time':rows[0]['pipeline_latency_seconds'] = True; seal(rows[0])
    elif mutation == 'nan_time':rows[0]['pipeline_latency_seconds'] = float('nan')
    elif mutation == 'bool_budget_count':snapshot['owned_attempts'] = False
    elif mutation == 'bool_budget_total':snapshot['arms']['case-01:rules']['attempts'] = False
    elif mutation == 'wrong_source':rows[0]['source'] = {}; seal(rows[0])
    elif mutation == 'duplicate_result':rows.append(deepcopy(rows[0]))
    elif mutation == 'wrong_controller':rows = [result(spec, 'rules', native['jev'])]
    elif mutation == 'invalid_pack_flag':rows[0]['pack']['verification']['independent_human'] = 0; seal(rows[0])
    elif mutation == 'fake_complete':rows[0]['pack'] = None; seal(rows[0])
    elif mutation == 'wrong_study':rows[0]['study_id'] = 'e' * 64; seal(rows[0])
    with pytest.raises(ValueError):
        summarize_study(spec, rows, snapshot)


@pytest.mark.parametrize('mutation', ['duplicate_receipt_owner', 'missing_owner_ref', 'false_total',
    'bool_call_bound', 'wrong_timing_request', 'unknown_timing', 'duplicate_reference'])
def test_accounting_tamper_cannot_drop_or_double_bill_physical_calls(study, mutation):
    spec, ledger = study
    first = paid(ledger)
    second = paid(ledger, request='second', stage='seed:2', rid='b' * 32)
    snapshot = ledger.inspect(); records = None
    if mutation == 'duplicate_receipt_owner':snapshot['calls'][1]['receipt']['reservation_id'] = snapshot['calls'][0]['receipt']['reservation_id']
    elif mutation == 'missing_owner_ref':snapshot['calls'][0]['references'] = snapshot['calls'][0]['references'][1:]
    elif mutation == 'false_total':snapshot['owned_micro_usd_charged_or_reserved'] = 0
    elif mutation == 'bool_call_bound':snapshot['calls'][0]['input_bound'] = True
    elif mutation == 'wrong_timing_request':records = [{'call_id': first, 'latency_seconds': 1, 'request_sha256': 'e' * 64}]
    elif mutation == 'unknown_timing':records = [{'call_id': 'f' * 32, 'latency_seconds': 1}]
    elif mutation == 'duplicate_reference':snapshot['calls'][0]['references'].append(deepcopy(snapshot['calls'][0]['references'][0]))
    with pytest.raises(ValueError):
        summarize_study(spec, [], snapshot, records)


def test_exact_native_shadow_state_and_sidecar_seal_cannot_be_rewritten(native):
    changed = deepcopy(native['jev'])
    record = changed['controller_decisions']['records'][0]
    record['state']['decision']['rounds_remaining'] = 0
    record['state_sha256'] = digest(record['state'])
    seal(changed['controller_decisions'])
    with pytest.raises(ValueError):
        validate_rule_shadow(changed)
