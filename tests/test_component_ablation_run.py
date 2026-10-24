"""Synthetic native-pipeline experiment runner; no real data, keys or HTTP."""
from copy import deepcopy
from dataclasses import asdict, replace
from hashlib import sha256
import json
from pathlib import Path

import pytest

from issueintel.benchmarks import component_ablation as registration
from issueintel.benchmarks import component_ablation_run as runner
from issueintel.investigation.providers import ProviderError
from issueintel.runtime.io import digest
from issueintel.repository.snapshot import SourceFile

from test_component_ablation import workspace, registered, authorization, historical, write


class Ledger:
    def __init__(self):
        self.rows = {}

    def receipt(self, identifier):
        return deepcopy(self.rows[identifier])


@pytest.fixture
def runtime(workspace, monkeypatch):
    root, catalog, contexts, accepted_calls = workspace
    ledger = Ledger()
    monkeypatch.setattr(runner.RuntimeConfig, 'ledger', lambda *a, **k: ledger)
    monkeypatch.setattr(runner, 'accepted_context', lambda root, cid: contexts[cid])
    calls = []

    class Provider:
        def __init__(self, config, *, on_receipt, call_context):
            self.config, self.on_receipt, self.context = config, on_receipt, call_context

        def complete_json(self, messages, *, max_output_tokens):
            return self.complete_structured(messages, max_output_tokens=max_output_tokens, schema=None)

        def complete_structured(self, messages, *, max_output_tokens, schema):
            index = len(calls)
            rid = f'{index + 1:032x}'
            record = {'reservation_id': rid, 'created_at': '2026-10-24T00:00:00Z',
                'model': self.config.model, 'input_bound': 10000, 'output_bound': max_output_tokens,
                'tokens_charged_or_reserved': 12048, 'micro_usd_charged_or_reserved': 100,
                'status': 'reserved', 'price_date': '2026-10-24',
                'observed_input_tokens': None, 'observed_output_tokens': None}
            ledger.rows[rid] = deepcopy(record)
            self.on_receipt(record)
            payload = json.loads(messages[1]['content'])
            calls.append({'payload': payload, 'schema': schema, 'context': self.context,
                          'model': self.config.model, 'effort': self.config.reasoning_effort,
                          'maximum': max_output_tokens})
            record.update(status='settled', tokens_charged_or_reserved=30,
                          observed_input_tokens=20, observed_output_tokens=10)
            ledger.rows[rid] = deepcopy(record)
            self.on_receipt(record)
            if 'case' in payload:
                code = next(row['id'] for row in payload['evidence'] if row['kind'] == 'code')
                return {'hypotheses': [{'id': f'H{i}', 'statement': f'Parser may mishandle value {i}',
                    'support_ids': [code], 'counter_ids': [], 'counter_status': 'not_found',
                    'falsification_test': f'Inspect unavailable sentinel {i}'} for i in range(1, 4)],
                    'abstention': {'label': 'uncertain', 'reason': 'Not executed'}}
            return {'reviews': [{'hypothesis_id': h['id'], 'assessment': 'uncertain',
                    'reason': 'No execution proof', 'evidence_ids': h['support_ids']}
                for h in payload['hypotheses']]}

    return root, contexts, ledger, calls, Provider


def setup(workspace, *, history=None, count=1):
    spec, directory, _ = registered(workspace, history=history)
    _, auth = authorization(workspace[0], spec, count=count)
    return spec, directory, auth


def records(root, directory, kind):
    return [runner._read(root, path) for path in (directory / 'cases').glob(f'*/{kind}/*.json')]


def test_native_pilot_shares_exact_generation_and_does_not_rerun_completed_arms(workspace, runtime, monkeypatch):
    root, _, _, calls, provider = runtime
    spec, directory, auth = setup(workspace)
    result = runner.run_component_ablation(root, spec['sha256'], auth, provider_factory=provider)
    assert result['completed_arms'] == 5 and result['gold_read'] is False
    assert not result['inference_sealed'] and result['stop_reason'] is None
    arms = {row['arm_id']: row for row in records(root, directory, 'arms')}
    assert arms['plus_history']['pack']['annotation'] == arms['plus_verifier']['pack']['annotation']
    assert arms['plus_history']['pack']['verification']['kind'] == 'structural'
    assert arms['plus_verifier']['pack']['verification']['kind'] == 'llm_separate_pass'
    shared = {row['stage_key'] for row in arms['plus_history']['stage_refs']} & {
              row['stage_key'] for row in arms['plus_verifier']['stage_refs']}
    assert shared
    assert any(ref['cache_hit'] for ref in arms['plus_verifier']['stage_refs'])
    assert all(row['effort'] == 'medium' and row['maximum'] == 2048 for row in calls)
    assert all(row['model'] == 'openai/gpt-oss-120b' for row in calls)
    count = len(calls)
    monkeypatch.setattr(runner, 'investigate', lambda *a, **k: pytest.fail('completed arm rerun'))
    repeated = runner.run_component_ablation(root, spec['sha256'], auth, provider_factory=provider)
    assert repeated['completed_arms'] == 5 and len(calls) == count


def test_missing_or_old_authorization_denies_before_provider_or_ledger(workspace, runtime, monkeypatch):
    root, _, _, calls, provider = runtime
    spec, directory, auth = setup(workspace)
    write(auth, {'scope': 'accepted_original_main_dev_paired_policy_diagnostic_v1'})
    monkeypatch.setattr(runner.RuntimeConfig, 'ledger', lambda *a: pytest.fail('ledger constructed'))
    with pytest.raises(ValueError, match='exact experiment'):
        runner.run_component_ablation(root, spec['sha256'], auth, provider_factory=provider)
    assert not calls and not (directory / 'cases').exists()


def test_expansion_requires_first_case_pilot_even_with_matrix_declaration(workspace, runtime):
    root, _, _, calls, provider = runtime
    spec, directory, auth = setup(workspace, count=25)
    with pytest.raises(ValueError, match='five first-case'):
        runner.run_component_ablation(root, spec['sha256'], auth, limit=25, provider_factory=provider)
    assert not calls


def test_full_matrix_seal_and_reopen_validate_shared_stage_and_receipt_owners(workspace, runtime):
    root, _, _, calls, provider = runtime
    spec, directory, auth = setup(workspace, count=25)
    runner.run_component_ablation(root, spec['sha256'], auth, provider_factory=provider)
    result = runner.run_component_ablation(root, spec['sha256'], auth, limit=25, provider_factory=provider)
    assert result['completed_arms'] == 125 and result['inference_sealed']
    saved = runner.load_sealed_inference(root, spec['sha256'])
    assert len(saved['results']) == 125 and saved['seal']['gold_read'] is False
    assert len(saved['calls']) == len(calls) < 125
    assert len({rid for call in saved['calls'] for rid in call['receipts']}) == len(calls)
    assert {row['owner_arm_id'] for row in saved['calls']} != {'lexical_symbol'}
    from issueintel.benchmarks.component_ablation_score import summarize_component_ablation
    targets = {row['case_id']: {'files': ['parser.py', 'outside.py'], 'available_files': ['parser.py'],
                               'patch_sha256': 'f' * 64} for row in spec['cases']}
    summary = summarize_component_ablation(saved['registration'], saved['results'], targets, saved['calls'])
    assert summary['semantic_quality'] == 'unreviewed' and summary['independent_human'] is False
    # A complete sealed result opens without constructing a provider or ledger.
    assert saved['seal']['terminal_results'] == 125
    first = saved['calls'][0]
    path = runner._case_directory(root, directory, first['case_id']) / 'calls' / (first['call_id'] + '.json')
    modified = deepcopy(first); modified['response']['invented'] = True
    write(path, runner._seal({k: v for k, v in modified.items() if k != 'sha256'}))
    with pytest.raises(ValueError):
        runner.load_sealed_inference(root, spec['sha256'])


def test_history_changes_real_model_inputs_but_verifier_pair_reuses_same_annotation(workspace, runtime):
    root, _, _, calls, provider = runtime
    row = registration._selected_rows(workspace[1])[0]
    spec, directory, auth = setup(workspace, history={row['case_id']: [historical(row['repository'])]})
    runner.run_component_ablation(root, spec['sha256'], auth, provider_factory=provider)
    arms = {row['arm_id']: row for row in records(root, directory, 'arms')}
    assert not any(e['kind'] == 'historical_issue' for e in arms['plus_graph']['pack']['evidence'])
    assert any(e['kind'] == 'historical_issue' for e in arms['plus_history']['pack']['evidence'])
    assert arms['plus_history']['pack']['annotation'] == arms['plus_verifier']['pack']['annotation']
    history_generations = [row for row in calls if 'case' in row['payload'] and
                           any(e['kind'] == 'historical_issue' for e in row['payload']['evidence'])]
    assert len(history_generations) == 1


def test_crash_after_completed_generation_reuses_timestamp_without_repeating_paid_call(workspace, runtime, monkeypatch):
    root, _, _, calls, provider = runtime
    spec, directory, auth = setup(workspace)
    original = runner._write
    def crash(root, path, value):
        if Path(path).parent.name == 'arms':
            raise KeyboardInterrupt('synthetic process death after completed native stages')
        return original(root, path, value)
    monkeypatch.setattr(runner, '_write', crash)
    with pytest.raises(KeyboardInterrupt):
        runner.run_component_ablation(root, spec['sha256'], auth, provider_factory=provider)
    stage = next(row for row in records(root, directory, 'stages') if row['binding']['stage'] == 'seed_generation')
    generated = deepcopy(stage['payload']['annotation'])
    count = len(calls)
    monkeypatch.setattr(runner, '_write', original)
    result = runner.run_component_ablation(root, spec['sha256'], auth, provider_factory=provider)
    assert result['completed_arms'] == 5
    first = next(row for row in records(root, directory, 'arms') if row['arm_id'] == 'lexical_symbol')
    assert first['pack']['annotation'] == generated
    assert len(calls) == count + 1  # Only the later critic is newly charged.


def test_crash_after_http_before_stage_commit_never_reconstructs_paid_stage(workspace, runtime, monkeypatch):
    root, _, _, calls, provider = runtime
    spec, directory, auth = setup(workspace)
    original = runner._write
    def crash(root, path, value):
        if value.get('schema') == runner.STAGE_SCHEMA and value.get('status') == 'completed' and value['binding']['stage'] == 'seed_generation':
            raise KeyboardInterrupt('synthetic process death before native payload publication')
        return original(root, path, value)
    monkeypatch.setattr(runner, '_write', crash)
    with pytest.raises(KeyboardInterrupt):
        runner.run_component_ablation(root, spec['sha256'], auth, provider_factory=provider)
    count = len(calls)
    monkeypatch.setattr(runner, '_write', original)
    with pytest.raises(ValueError, match='Interrupted native stage'):
        runner.run_component_ablation(root, spec['sha256'], auth, provider_factory=provider)
    assert len(calls) == count == 1


def test_unknown_reservation_survives_and_prevents_automatic_restart(workspace, runtime):
    root, _, ledger, calls, provider = runtime
    spec, directory, auth = setup(workspace)
    class FailedProvider(provider):
        def complete_structured(self, messages, *, max_output_tokens, schema):
            rid = 'f' * 32
            record = {'reservation_id': rid, 'created_at': '2026-10-24T00:00:00Z', 'model': self.config.model,
                'input_bound': 100, 'output_bound': 2048, 'tokens_charged_or_reserved': 2148,
                'micro_usd_charged_or_reserved': 100, 'status': 'reserved', 'price_date': '2026-10-24',
                'observed_input_tokens': None, 'observed_output_tokens': None}
            ledger.rows[rid] = deepcopy(record); self.on_receipt(record)
            calls.append({'unknown': True})
            raise ProviderError('Synthetic unknown paid outcome')
    result = runner.run_component_ablation(root, spec['sha256'], auth, provider_factory=FailedProvider)
    assert result['failed_arms'] == 1 and result['stop_reason'] and not result['inference_sealed']
    assert records(root, directory, 'calls')[0]['receipts']['f' * 32]['status'] == 'reserved'
    inspected = runner.inspect_component_run(root, spec['sha256'])
    assert inspected['saved_record_integrity_valid']
    assert inspected['receipt_status_counts'] == {'reserved': 1}
    assert inspected['blocked_reason'] == 'unknown_or_breached_paid_outcome_manual_audit_required'
    with pytest.raises(ValueError, match='Paid outcome remains unknown'):
        runner.run_component_ablation(root, spec['sha256'], auth, provider_factory=provider)
    assert len(calls) == 1


def test_source_edits_after_registration_deny_before_model(workspace, runtime):
    root, _, _, calls, provider = runtime
    spec, directory, auth = setup(workspace)
    (root / 'src/issueintel/synthetic.py').write_text('VERSION=2')
    with pytest.raises(ValueError, match='code or configuration changed'):
        runner.run_component_ablation(root, spec['sha256'], auth, provider_factory=provider)
    assert not calls


def test_changed_ledger_receipt_blocks_continuation_without_repeating_model(workspace, runtime):
    root, _, ledger, calls, provider = runtime
    spec, directory, auth = setup(workspace)
    runner.run_component_ablation(root, spec['sha256'], auth, provider_factory=provider)
    count = len(calls)
    next(iter(ledger.rows.values()))['tokens_charged_or_reserved'] += 1
    with pytest.raises(ValueError, match='Owned ledger receipt changed'):
        runner.run_component_ablation(root, spec['sha256'], auth, provider_factory=provider)
    assert len(calls) == count


def test_terminal_invalid_generation_is_frozen_and_shared_without_paid_retries(workspace, runtime):
    root, _, _, calls, provider = runtime
    spec, directory, auth = setup(workspace)
    class InvalidProvider(provider):
        def complete_structured(self, messages, *, max_output_tokens, schema):
            super().complete_structured(messages, max_output_tokens=max_output_tokens, schema=schema)
            return {'hypotheses': [], 'abstention': {'label': 'uncertain', 'reason': 'Synthetic invalid response'}}
    result = runner.run_component_ablation(root, spec['sha256'], auth, provider_factory=InvalidProvider)
    assert len(calls) == 2  # Exactly the native seed plus one schema repair.
    assert result['failed_arms'] == 1 and result['unavailable_arms'] == 4
    assert result['stop_reason'] is None and not result['inference_sealed']
    assert len(records(root, directory, 'calls')) == 2
    repeated = runner.run_component_ablation(root, spec['sha256'], auth, provider_factory=provider)
    assert len(calls) == 2 and repeated['failed_arms'] == 1


def test_native_counter_search_adds_source_and_owns_refined_generation(workspace, runtime):
    root, contexts, _, calls, provider = runtime
    selected = registration._selected_rows(workspace[1])[0]
    case, snapshot, receipt = contexts[selected['case_id']]
    text = 'def sentinel(unavailable):\n    return unavailable if unavailable is not None else []\n'
    updated = replace(snapshot, files=snapshot.files + (SourceFile('sentinel.py', text, sha256(text.encode()).hexdigest()),))
    unsealed = asdict(updated); unsealed.pop('snapshot_sha256')
    contexts[selected['case_id']] = (case, replace(updated, snapshot_sha256=digest(unsealed)), receipt)
    spec, directory, auth = setup(workspace)
    result = runner.run_component_ablation(root, spec['sha256'], auth, provider_factory=provider)
    assert result['completed_arms'] == 5
    counter = next(row for row in records(root, directory, 'arms') if row['arm_id'] == 'plus_counter_search')
    assert counter['pack']['refinement']['generation_passes'] == 2
    assert any(e['locator'].startswith('sentinel.py:') for e in counter['pack']['evidence'])
    assert counter['pack']['refinement']['search']['counter_evidence_confirmed'] is False
    refined = [row for row in records(root, directory, 'stages') if row['binding']['stage'] == 'refined_generation']
    assert len(refined) == 1 and refined[0]['owner_arm_id'] == 'plus_counter_search'
    assert len(refined[0]['call_ids']) == 1
    assert all(len(row['call_ids']) <= 5 for row in records(root, directory, 'arms'))


def test_invalid_closed_critic_is_terminal_output_failure_without_global_unknown_stop(workspace, runtime):
    root, _, _, calls, provider = runtime
    spec, directory, auth = setup(workspace)
    class InvalidCritic(provider):
        def complete_structured(self, messages, *, max_output_tokens, schema):
            answer = super().complete_structured(messages, max_output_tokens=max_output_tokens, schema=schema)
            if 'hypotheses' in json.loads(messages[1]['content']):
                return {'reviews': []}
            return answer
    result = runner.run_component_ablation(root, spec['sha256'], auth, provider_factory=InvalidCritic)
    assert result['completed_arms'] == 3 and result['failed_arms'] == 1 and result['unavailable_arms'] == 1
    assert len(calls) == 2 and result['stop_reason'] is None
    failed = next(row for row in records(root, directory, 'arms') if row['status'] == 'failed')
    assert failed['failure']['error_type'] == 'CriticOutputRejected'
    stage = next(row for row in records(root, directory, 'stages') if row['status'] == 'failed')
    assert stage['terminal_output_failure'] and len(stage['call_ids']) == 1
    again = runner.run_component_ablation(root, spec['sha256'], auth, provider_factory=provider)
    assert again['failed_arms'] == 1 and len(calls) == 2


def test_rehashed_pack_evidence_is_rejected_by_native_readonly_replay(workspace, runtime):
    root, _, _, calls, provider = runtime
    spec, directory, auth = setup(workspace, count=25)
    runner.run_component_ablation(root, spec['sha256'], auth, provider_factory=provider)
    arms = records(root, directory, 'arms')
    row = arms[0]
    code = next(e for e in row['pack']['evidence'] if e['kind'] == 'code')
    code['text'] += '\nInvented code'
    code['sha256'] = sha256(code['text'].encode()).hexdigest()
    row = runner._seal({key: value for key, value in row.items() if key != 'sha256'})
    write(runner._arm_path(root, directory, row['case_id'], row['arm_id']), row)
    _, outcomes, stored_calls, stored_stages = runner._records(root, spec, directory)
    with pytest.raises(ValueError, match='does not replay exactly'):
        runner._replay_completed_packs(root, spec, directory, outcomes, stored_stages)


def test_partial_pilot_is_not_scoring_seal(workspace, runtime):
    root, _, _, calls, provider = runtime
    spec, directory, auth = setup(workspace)
    runner.run_component_ablation(root, spec['sha256'], auth, provider_factory=provider)
    with pytest.raises(FileNotFoundError):
        runner.load_sealed_inference(root, spec['sha256'])


def test_complete_matrix_reference_guard_rejects_orphan_stages_and_calls(workspace, runtime):
    root, _, _, calls, provider = runtime
    spec, directory, auth = setup(workspace)
    runner.run_component_ablation(root, spec['sha256'], auth, provider_factory=provider)
    _, arms, paid, stages = runner._records(root, spec, directory)
    assert runner._matrix_references(arms, paid, stages)
    extra = deepcopy(stages[0]); extra['stage_key'] = 'f' * 64
    with pytest.raises(ValueError, match='unreferenced'):
        runner._matrix_references(arms, paid, stages + [extra])
    extra_call = deepcopy(paid[0]); extra_call['call_id'] = 'f' * 64
    with pytest.raises(ValueError, match='unreferenced'):
        runner._matrix_references(arms, paid + [extra_call], stages)
    bad_arms = deepcopy(arms)
    shared = next(stage for stage in stages if sum(any(ref['stage_key'] == stage['stage_key']
                  for ref in arm['stage_refs']) for arm in arms) > 1)
    owner = next(arm for arm in bad_arms if arm['arm_id'] == shared['owner_arm_id'])
    owner['stage_refs'] = [ref for ref in owner['stage_refs'] if ref['stage_key'] != shared['stage_key']]
    with pytest.raises(ValueError, match='owner has no terminal'):
        runner._matrix_references(bad_arms, paid, stages)


@pytest.mark.parametrize('change', [
    {'observed_input_tokens': None},
    {'observed_input_tokens': None, 'observed_output_tokens': None},
    {'tokens_charged_or_reserved': 31},
    {'observed_input_tokens': 20000, 'tokens_charged_or_reserved': 20010},
])
def test_malformed_settled_usage_denied_before_scoring(change):
    receipt = {'reservation_id': 'a' * 32, 'created_at': '2026-10-24T00:00:00Z',
        'model': 'openai/gpt-oss-120b', 'input_bound': 10000, 'output_bound': 2048,
        'tokens_charged_or_reserved': 30, 'micro_usd_charged_or_reserved': 100,
        'status': 'settled', 'price_date': '2026-10-24',
        'observed_input_tokens': 20, 'observed_output_tokens': 10}
    receipt.update(change)
    with pytest.raises(ValueError, match='usage is inconsistent'):
        runner._receipt(receipt, 'openai/gpt-oss-120b')


def test_run_inspection_is_readonly_when_source_is_stale_and_never_opens_ledger(workspace, runtime, monkeypatch):
    root, _, _, calls, provider = runtime
    spec, directory, auth = setup(workspace)
    runner.run_component_ablation(root, spec['sha256'], auth, provider_factory=provider)
    inventory = runner._inventory(root, directory)
    (root / 'src/issueintel/synthetic.py').write_text('VERSION=3')
    monkeypatch.setattr(runner.RuntimeConfig, 'ledger', lambda *a: pytest.fail('ledger opened by inspection'))
    monkeypatch.setattr(runner.RuntimeConfig, 'load', lambda *a: pytest.fail('runtime loaded by inspection'))
    result = runner.inspect_component_run(root, spec['sha256'])
    assert result['terminal_arms'] == 5 and result['terminal_arm_counts'] == {'completed': 5}
    assert result['blocked_reason'] is None and not result['provider_or_ledger_constructed']
    assert result['native_source_replay_performed'] is False
    assert runner._inventory(root, directory) == inventory
    assert 'problem_statement' not in json.dumps(result) and 'Parser may' not in json.dumps(result)


def test_request_limits_are_strict_integer_before_source_access(tmp_path):
    for value in (0, 26, True, '1', None):
        with pytest.raises(ValueError):
            runner.run_component_ablation(tmp_path, 'a' * 64, 'missing', limit=value)
