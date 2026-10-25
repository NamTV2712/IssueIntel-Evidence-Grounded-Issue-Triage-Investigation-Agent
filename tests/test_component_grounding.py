"""Synthetic common grounding assessment; no dataset, credentials or HTTP."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from issueintel.benchmarks import component_grounding as grounding
from issueintel.benchmarks.component_ablation import ARMS
from issueintel.investigation.providers import ProviderError
from issueintel.investigation.verifier import verify_hypotheses
from issueintel.runtime.config import RuntimeConfig
from issueintel.runtime.io import digest


def sealed(value):
    return {**value, 'sha256': digest(value)}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, ensure_ascii=False) + '\n', encoding='utf-8')
    return path


def native_pack(case_id, flavor):
    text = 'def parse(value):\n    return value[0]\n'
    evidence = [{'id': 'code1', 'kind': 'code', 'locator': 'parser.py:1-2', 'text': text,
        'sha256': sha256(text.encode()).hexdigest(), 'revision': 'a' * 40,
        'provenance': {'kind': 'code', 'local_receipt': 'LOCAL_RECEIPT_DO_NOT_SEND'}}]
    annotation = {'hypotheses': [{'id': f'H{index}',
        'statement': f'{case_id} {flavor}: parser may mishandle value {index}',
        'support_ids': ['code1'], 'counter_ids': [], 'counter_status': 'not_found',
        'falsification_test': f'Inspect empty input {index}'} for index in range(1, 4)],
        'source_quality': {}, 'provenance': {'provider': 'groq', 'model': 'openai/gpt-oss-120b',
            'receipt_metadata': 'ANNOTATION_RECEIPT_DO_NOT_SEND', 'repair_count': 0}}
    return {'case': {'case_id': case_id, 'split': 'main_dev'},
        'snapshot': {'protocol': 'issue_time'}, 'evidence': evidence, 'annotation': annotation,
        'verification': {**verify_hypotheses(annotation, evidence),
                         'old_critic_marker': 'OLD_CRITIC_DO_NOT_SEND'},
        'leakage_audit': {'gold_in_inference': False, 'strict_issue_time_verified': True}}


def synthetic_parent():
    cases = [{'case_id': f'verified:synthetic__case-{index:02d}'} for index in range(25)]
    registration = sealed({'cases': cases, 'arms': [{'arm_id': arm} for arm in ARMS],
        'runtime': {'model': 'openai/gpt-oss-120b', 'reasoning_effort': 'medium',
                    'max_output_tokens': 2048, 'context_tokens': 131072},
        'limits': {'max_provider_calls_per_arm': 5, 'max_provider_calls_total': 625},
        'protocol_sha256': 'b' * 64, 'fingerprint': {}})
    adapter_folder = Path(__file__).resolve().parents[1] / 'src/issueintel/benchmarks'
    adapter_sources = [{'path': str(adapter_folder / name),
                        'sha256': sha256((adapter_folder / name).read_bytes()).hexdigest()}
        for name in ('component_continuation.py', 'component_continuation_run.py', 'component_continuation_score.py')]
    parent_plan = sealed({'registration_sha256': registration['sha256'], 'case_ids': [row['case_id'] for row in cases],
                          'adapter_sources': sorted(adapter_sources, key=lambda row: row['path'])})
    results = []
    for index, case in enumerate(cases):
        for arm in ARMS:
            flavor = {'lexical_symbol': 'A', 'plus_graph': 'B', 'plus_history': 'C',
                      'plus_verifier': 'C', 'plus_counter_search': 'D'}[arm]
            status = 'failed' if index == 24 and arm == 'plus_counter_search' else (
                'unavailable' if index == 23 and arm == 'lexical_symbol' else 'completed')
            results.append(sealed({'case_id': case['case_id'], 'arm_id': arm, 'status': status,
                'pack': native_pack(case['case_id'], flavor) if status == 'completed' else None}))
    inference_seal = sealed({'schema': 'component_continuation_inference_seal_v2',
        'registration_sha256': registration['sha256'], 'continuation_plan_sha256': parent_plan['sha256'],
        'selected_case_ids': parent_plan['case_ids'], 'arms': list(ARMS), 'terminal_results': 125,
        'completed_results': 123,
        'effective_results': {row['case_id'] + '/' + row['arm_id']: row['sha256'] for row in results},
        'gold_read': False, 'quality_claim': False, 'holdout_or_live_access': False})
    return {'registration': registration, 'plan': parent_plan, 'seal': inference_seal,
            'results': results, 'calls': []}


def refresh_parent(state):
    """Re-seal a deliberately invalid semantic fixture, avoiding hash-only tests."""
    for row in state['results']:
        row['sha256'] = digest({key: item for key, item in row.items() if key != 'sha256'})
    registration = state['registration']
    registration['sha256'] = digest({key: item for key, item in registration.items() if key != 'sha256'})
    plan = state['plan']
    plan.update(registration_sha256=registration['sha256'],
                case_ids=[row['case_id'] for row in registration['cases']])
    plan['sha256'] = digest({key: item for key, item in plan.items() if key != 'sha256'})
    saved = state['seal']
    saved.update(registration_sha256=registration['sha256'], continuation_plan_sha256=plan['sha256'],
        selected_case_ids=plan['case_ids'], terminal_results=len(state['results']),
        completed_results=sum(row['status'] == 'completed' for row in state['results']),
        effective_results={row['case_id'] + '/' + row['arm_id']: row['sha256'] for row in state['results']})
    saved['sha256'] = digest({key: item for key, item in saved.items() if key != 'sha256'})


class FakeLedger:
    def __init__(self):
        self.rows = {}

    def receipt(self, reservation_id):
        return deepcopy(self.rows[reservation_id])

    def records(self):
        return list(deepcopy(self.rows).values())


@pytest.fixture
def study(tmp_path, monkeypatch):
    root = tmp_path
    configs = root / 'configs'; configs.mkdir()
    (configs / 'runtime.toml').write_text(
        'provider = "groq"\nmodel = "openai/gpt-oss-120b"\nmax_output_tokens = 4096\n'
        'daily_total_tokens = 100000\nmonthly_usd = 2\n'
        'price_file = "configs/prices.json"\nledger = "artifacts/ledger.sqlite"\n', encoding='utf-8')
    today = datetime.now(timezone.utc).date().isoformat()
    write_json(configs / 'prices.json', {'checked_at': today, 'currency': 'USD',
        'source': 'https://console.groq.com/docs/models',
        'models': {'openai/gpt-oss-120b': {'input_per_million': '0.1', 'output_per_million': '0.2',
            'context_tokens': 131072, 'max_output_tokens': 32768}}})
    state, events, calls, ledger = synthetic_parent(), [], [], FakeLedger()
    def open_ledger(*args, **kwargs):
        events.append('ledger')
        return ledger
    monkeypatch.setattr(RuntimeConfig, 'ledger', open_ledger)
    monkeypatch.setattr('socket.create_connection', lambda *args, **kwargs: pytest.fail('HTTP attempted'))

    def load_parent(actual_root, identifier):
        assert Path(actual_root).resolve() == root.resolve()
        assert identifier == state['plan']['sha256']
        events.append('parent_replay')
        return deepcopy(state)

    def context_guard(actual_root, plan, parent, unit):
        assert Path(actual_root).resolve() == root.resolve()
        assert parent == state
        events.append('context_guard')

    class Provider:
        mode = 'success'
        fixed_rid = None

        def __init__(self, config, *, on_receipt, call_context):
            self.config, self.on_receipt, self.context = config, on_receipt, call_context

        def complete_structured(self, messages, *, max_output_tokens, schema):
            payload = json.loads(messages[1]['content'])
            rid = self.fixed_rid or f'{len(calls) + 1:032x}'
            receipt = {'reservation_id': rid, 'created_at': today + 'T00:00:00Z',
                'model': self.config.model, 'input_bound': 10000, 'output_bound': max_output_tokens,
                'tokens_charged_or_reserved': 12048, 'micro_usd_charged_or_reserved': 100,
                'status': 'reserved', 'price_date': today,
                'observed_input_tokens': None, 'observed_output_tokens': None}
            ledger.rows[rid] = deepcopy(receipt); self.on_receipt(deepcopy(receipt))
            calls.append({'messages': deepcopy(messages), 'schema': deepcopy(schema),
                'context': deepcopy(self.context), 'model': self.config.model,
                'effort': self.config.reasoning_effort, 'maximum': max_output_tokens})
            events.append('provider')
            if self.mode == 'unknown':
                raise ProviderError('Synthetic unknown outcome')
            receipt.update(status='failed_bound' if self.mode == 'transport' else 'settled',
                tokens_charged_or_reserved=12048 if self.mode == 'transport' else 30,
                observed_input_tokens=None if self.mode == 'transport' else 20,
                observed_output_tokens=None if self.mode == 'transport' else 10)
            ledger.rows[rid] = deepcopy(receipt); self.on_receipt(deepcopy(receipt))
            if self.mode == 'transport':
                raise ProviderError('Synthetic closed request failure', diagnostics={
                    'failure_phase': 'request', 'transport_error_type': 'RemoteDisconnected'})
            if self.mode == 'schema':
                return {'reviews': []}
            return {'reviews': [{'hypothesis_id': row['id'], 'assessment': 'uncertain',
                'reason': 'No execution proof', 'evidence_ids': row['support_ids']}
                for row in payload['hypotheses']]}

        def complete_json(self, messages, *, max_output_tokens):
            raise AssertionError('Common judge must use the frozen structured schema')

    return {'root': root, 'state': state, 'events': events, 'calls': calls, 'ledger': ledger,
            'provider': Provider, 'load_parent': load_parent, 'context_guard': context_guard}


def prepare(study):
    plan = grounding.create_grounding_plan(study['root'], study['state']['plan']['sha256'],
                                           parent_loader=study['load_parent'])
    path = grounding.save_grounding_plan(study['root'], plan, parent_loader=study['load_parent'])
    return plan, path


def authorization(study, plan, *, count=1):
    selected = set(plan['case_ids'][:count])
    value = {'schema': 'component_grounding_run_authorization_v1',
        'scope': 'accepted_original_main_dev_component_grounding_v1',
        'plan_sha256': plan['sha256'], 'parent_plan_sha256': plan['parent_plan_sha256'],
        'parent_inference_seal_sha256': plan['parent_inference_seal_sha256'],
        'registration_sha256': plan['registration_sha256'], 'case_ids': plan['case_ids'],
        'unit_ids': [unit['unit_id'] for unit in plan['units']], **plan['judge_config'],
        'authorized_case_count': count,
        'phase': 'first_case_pilot' if count == 1 else 'registered_matrix',
        'max_provider_calls_total': sum(bool(selected.intersection(unit['case_ids'])) for unit in plan['units']),
        'execution_authorized': True, 'gold_access_authorized': False,
        'holdout_or_live_access': False, 'jev_spending_authorized': False,
        'author_request': 'Synthetic explicit common model assessment only',
        'date': datetime.now(timezone.utc).date().isoformat()}
    path = study['root'] / 'artifacts' / f'grounding-{count}-authorization.json'
    return value, write_json(path, value)


def run(study, plan, auth, *, limit=1, provider=None, **kwargs):
    return grounding.run_grounding(study['root'], plan['sha256'], auth, limit=limit,
        provider_factory=provider or study['provider'], config_factory=RuntimeConfig.load,
        parent_loader=study['load_parent'], context_guard=study['context_guard'], **kwargs)


def test_preview_freezes_all_arms_and_deduplicates_exact_native_projection_without_calls(study, monkeypatch):
    root = study['root']
    (root / '.env').write_text('DO_NOT_READ=not_a_real_secret', encoding='utf-8')
    from issueintel.runtime import io
    monkeypatch.setattr(io, 'load_local_env', lambda *args: pytest.fail('preview read credentials'))
    original_open = Path.open
    def guarded(path, *args, **kwargs):
        if path.name == '.env':
            pytest.fail('preview read credentials')
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', guarded)
    plan, path = prepare(study)
    again = grounding.create_grounding_plan(root, study['state']['plan']['sha256'], parent_loader=study['load_parent'])
    assert again == plan
    assert Path(path).is_file()
    assert len(plan['case_ids']) == 25 and len(plan['arm_bindings']) == 125
    assert len(plan['units']) == 98
    assert 'ledger' not in study['events'] and not study['calls']
    assert {row['source_status'] for row in plan['arm_bindings']} == {'completed', 'failed', 'unavailable'}
    assert sum(row['unit_id'] is None for row in plan['arm_bindings']) == 2
    for case_id in plan['case_ids']:
        bindings = {row['arm_id']: row for row in plan['arm_bindings'] if row['case_id'] == case_id}
        assert bindings['plus_history']['unit_id'] == bindings['plus_verifier']['unit_id']
    assert plan['judge_config'] == {'provider': 'groq', 'model': 'openai/gpt-oss-120b',
        'reasoning_effort': 'medium', 'max_output_tokens': 2048}
    for unit in plan['units']:
        raw = json.dumps(unit['request'])
        assert not any(marker in raw for marker in ('DO_NOT_SEND', 'old_critic_marker', 'plus_verifier', 'plus_counter_search'))
        payload = json.loads(unit['request']['messages'][1]['content'])
        assert set(payload) == {'hypotheses', 'evidence'}
        assert unit['request']['schema']['properties']['reviews']
    grounding.inspect_grounding(root, plan['sha256'], parent_loader=study['load_parent'])
    assert 'ledger' not in study['events'] and not study['calls']


def test_native_request_binding_is_stable_across_python_hash_seeds():
    repository = Path(__file__).resolve().parents[1]
    script = (
        'from test_component_grounding import native_pack\n'
        'from issueintel.benchmarks.component_grounding import _project, _digest\n'
        'print(_digest(_project(native_pack("verified:synthetic__case-00", "A"))[1]))\n')
    results = []
    for seed in ('41', '73'):
        environment = {**os.environ, 'PYTHONHASHSEED': seed,
            'PYTHONPATH': os.pathsep.join((str(repository / 'src'), str(repository / 'tests')))}
        results.append(subprocess.check_output([sys.executable, '-c', script],
            cwd=repository, env=environment, text=True).strip())
    assert results[0] == results[1]


@pytest.mark.parametrize('change', ['missing_arm', 'wrong_split', 'unsealed', 'gold_flag', 'duplicate_case'])
def test_parent_admission_rejects_incomplete_or_wrong_boundary_before_spending(study, change):
    state = study['state']
    if change == 'missing_arm':
        state['results'].pop()
    elif change == 'wrong_split':
        state['results'][0]['pack']['case']['split'] = 'main_test'
    elif change == 'unsealed':
        state.pop('seal')
    elif change == 'gold_flag':
        state['seal']['gold_read'] = True
    else:
        state['registration']['cases'][1] = deepcopy(state['registration']['cases'][0])
    if change != 'unsealed':
        refresh_parent(state)
    with pytest.raises((ValueError, KeyError)):
        grounding.create_grounding_plan(study['root'], state['plan']['sha256'], parent_loader=study['load_parent'])
    assert not study['calls'] and 'ledger' not in study['events']


def test_restricted_annotation_is_rejected_before_projection(study):
    state = study['state']
    arm = state['results'][0]
    arm['pack']['annotation']['source_quality'] = {'human_rationale': 'private source rationale'}
    arm['sha256'] = digest({key: item for key, item in arm.items() if key != 'sha256'})
    state['seal']['effective_results'][arm['case_id'] + '/' + arm['arm_id']] = arm['sha256']
    state['seal']['sha256'] = digest({key: item for key, item in state['seal'].items() if key != 'sha256'})
    with pytest.raises(ValueError):
        grounding.create_grounding_plan(study['root'], state['plan']['sha256'], parent_loader=study['load_parent'])
    assert not study['calls']


@pytest.mark.parametrize('field,value', [
    ('gold_access_authorized', True), ('holdout_or_live_access', True),
    ('jev_spending_authorized', True), ('execution_authorized', False),
    ('model', 'other-model'), ('max_output_tokens', 4096),
    ('max_provider_calls_total', 125), ('authorized_case_count', True),
    ('date', 'invalid'), ('scope', 'different-experiment')])
def test_exact_authorization_denies_before_environment_or_provider(study, monkeypatch, field, value):
    plan, _ = prepare(study)
    declared, path = authorization(study, plan)
    declared[field] = value; write_json(path, declared)
    before = list(study['events'])
    from issueintel.runtime import io
    monkeypatch.setattr(io, 'load_local_env', lambda *args: pytest.fail('denied execution read credentials'))
    with pytest.raises(ValueError):
        run(study, plan, path, load_environment=True)
    assert not study['calls'] and study['events'].count('ledger') == before.count('ledger')


def test_matrix_requires_reviewed_pilot_before_environment_or_provider(study, monkeypatch):
    plan, _ = prepare(study)
    _, path = authorization(study, plan, count=25)
    from issueintel.runtime import io
    monkeypatch.setattr(io, 'load_local_env', lambda *args: pytest.fail('matrix opened before pilot'))
    with pytest.raises(ValueError):
        run(study, plan, path, limit=25, load_environment=True)
    assert not study['calls'] and 'ledger' not in study['events']


def test_pilot_then_full_matrix_owns_one_call_per_unit_and_reopens_without_new_calls(study):
    plan, _ = prepare(study)
    _, pilot_auth = authorization(study, plan)
    pilot = run(study, plan, pilot_auth)
    assert pilot['completed_units'] == 4 and pilot['failed_units'] == 0
    assert not pilot['study_sealed'] and len(study['calls']) == 4
    assert run(study, plan, pilot_auth)['completed_units'] == 4 and len(study['calls']) == 4
    _, matrix_auth = authorization(study, plan, count=25)
    matrix = run(study, plan, matrix_auth, limit=25)
    assert matrix['completed_units'] == 98 and matrix['failed_units'] == 0
    assert matrix['study_sealed'] and matrix['provider_calls'] == 98
    assert len(study['calls']) == len(study['ledger'].rows) == 98
    assert all(call['model'] == 'openai/gpt-oss-120b' and call['effort'] == 'medium'
               and call['maximum'] == 2048 for call in study['calls'])
    assert all(call['context']['category'] == 'ARTIFACT_GENERATION_COST'
               and call['context']['stage'] == 'component_grounding_judge' for call in study['calls'])
    assert run(study, plan, matrix_auth, limit=25)['provider_calls'] == 98
    assert len(study['calls']) == 98
    report = grounding.score_grounding(study['root'], plan['sha256'], parent_loader=study['load_parent'])
    assert report['case_clusters'] == 25 and report['arm_results'] == 125
    assert report['unique_judge_units'] == 98
    for arm, values in report['by_arm'].items():
        assert values['selected_cases'] == 25
        expected = 24 if arm in {'lexical_symbol', 'plus_counter_search'} else 25
        assert values['reviewed_cases'] == expected
        assert values['source_unavailable_cases'] == 25 - expected
        assert values['judge_failed_cases'] == 0
        assert values['assessment_counts']['uncertain'] == expected * 3
        assert values['macro_case_fractions']['uncertain'] == 1
    pairs = report['paired_comparisons']
    if isinstance(pairs, dict):
        pairs = list(pairs.values())
    assert len(pairs) == 10
    assert {row['common_reviewed_cases'] for row in pairs} == {23, 24, 25}
    assert report['independent_human'] is False
    assert report['semantic_accuracy_verified'] is False
    assert report['causal_truth_verified'] is False
    assert len(study['calls']) == 98


@pytest.mark.parametrize('mode', ['transport', 'schema'])
def test_closed_failure_or_invalid_response_is_terminal_without_output_repair(study, mode):
    plan, _ = prepare(study)
    _, path = authorization(study, plan)
    study['provider'].mode = mode
    result = run(study, plan, path)
    assert result['failed_units'] >= 1
    assert len(study['calls']) <= 4
    assert run(study, plan, path)['failed_units'] >= result['failed_units']
    assert len(study['calls']) <= 4
    assert len({digest(call['messages']) for call in study['calls']}) == len(study['calls'])
    if mode == 'transport':
        assert all(row['status'] == 'failed_bound' for row in study['ledger'].rows.values())


def test_unknown_receipt_stops_before_any_other_unit_and_never_paid_retries(study):
    plan, _ = prepare(study)
    _, path = authorization(study, plan)
    study['provider'].mode = 'unknown'
    try:
        result = run(study, plan, path)
        assert result['stop_reason']
    except ValueError:
        pass
    assert len(study['calls']) == 1
    assert next(iter(study['ledger'].rows.values()))['status'] == 'reserved'
    try:
        run(study, plan, path)
    except ValueError:
        pass
    assert len(study['calls']) == 1


def test_http_return_without_committed_native_review_cannot_be_retried(study, monkeypatch):
    plan, _ = prepare(study)
    _, path = authorization(study, plan)
    original = grounding._write_unit
    def crash(root, path, value, *, new=False):
        if value.get('status') == 'reviewed':
            raise KeyboardInterrupt('Synthetic crash after HTTP before native review publication')
        return original(root, path, value, new=new)
    monkeypatch.setattr(grounding, '_write_unit', crash)
    with pytest.raises(KeyboardInterrupt):
        run(study, plan, path)
    assert len(study['calls']) == 1
    monkeypatch.setattr(grounding, '_write_unit', original)
    with pytest.raises(ValueError):
        run(study, plan, path)
    assert len(study['calls']) == 1


def test_changed_parent_is_denied_before_another_call(study):
    plan, _ = prepare(study)
    _, path = authorization(study, plan)
    study['state']['results'][0]['pack']['evidence'][0]['text'] += '# changed'
    with pytest.raises(ValueError):
        run(study, plan, path)
    assert not study['calls'] and 'ledger' not in study['events']


def test_executing_module_location_must_match_approved_plan_before_environment(study, monkeypatch):
    plan, _ = prepare(study)
    _, path = authorization(study, plan)
    alternate = study['root'] / 'different-adapter/component_grounding.py'
    alternate.parent.mkdir()
    alternate.write_bytes(Path(grounding.__file__).read_bytes())
    monkeypatch.setattr(grounding, '__file__', str(alternate))
    from issueintel.runtime import io
    monkeypatch.setattr(io, 'load_local_env', lambda *args: pytest.fail('wrong adapter read credentials'))
    with pytest.raises(ValueError):
        run(study, plan, path, load_environment=True)
    assert not study['calls'] and 'ledger' not in study['events']


def test_one_receipt_cannot_be_owned_by_different_judge_units(study):
    plan, _ = prepare(study)
    _, path = authorization(study, plan)
    study['provider'].fixed_rid = 'e' * 32
    try:
        result = run(study, plan, path)
        assert not result['study_sealed']
    except ValueError:
        pass
    assert len(study['calls']) <= 2
    count = len(study['calls'])
    try:
        run(study, plan, path)
    except ValueError:
        pass
    assert len(study['calls']) == count


def test_alias_unit_replays_every_mapped_case_before_provider(study, monkeypatch):
    state = study['state']
    first, second = [row['case_id'] for row in state['registration']['cases'][:2]]
    for row in state['results']:
        if row['case_id'] == second:
            for hypothesis in row['pack']['annotation']['hypotheses']:
                hypothesis['statement'] = hypothesis['statement'].replace(second, first)
    refresh_parent(state)
    plan, _ = prepare(study)
    unit = next(row for row in plan['units'] if row['case_ids'] == [first, second])
    assert len(plan['units']) == 94
    state['directory'] = study['root'] / 'artifacts/synthetic-parent'
    state['base_directory'] = study['root'] / 'artifacts/synthetic-base'
    replayed = []
    def replay(root, spec, row, sources):
        replayed.append(row['case_id'])
        if row['case_id'] == second:
            raise ValueError('Synthetic second-case source changed')
    core = SimpleNamespace(validate_plan=lambda *args, **kwargs: None,
                           read=lambda *args: state['seal'])
    registration = SimpleNamespace(load_history_sources=lambda *args: {row['case_id']: []
        for row in state['registration']['cases']})
    parent = SimpleNamespace(_core=lambda: core, _inference_inventory=lambda *args: {},
                             _api=lambda: (registration, SimpleNamespace(_replay_context=replay)))
    monkeypatch.setattr(grounding, '_parent', lambda: parent)
    with pytest.raises(ValueError, match='second-case'):
        grounding._fast_guard(study['root'], plan, state, unit)
    assert replayed == [first, second]
    assert not study['calls']


@pytest.mark.parametrize('suffix', ['.json.backup', ''])
def test_aliased_or_unknown_unit_filename_is_rejected(study, suffix):
    plan, _ = prepare(study)
    _, path = authorization(study, plan)
    run(study, plan, path)
    directory = grounding.grounding_directory(study['root'], plan) / 'units'
    original = next(directory.glob('*.json'))
    alias = directory / (original.stem + suffix)
    alias.write_bytes(original.read_bytes())
    with pytest.raises(ValueError):
        grounding.inspect_grounding(study['root'], plan['sha256'], parent_loader=study['load_parent'])
    assert len(study['calls']) == 4


@pytest.mark.parametrize('namespace', ['backup', 'scores', 'execution-intents'])
def test_moving_a_paid_journal_cannot_hide_an_attempt_and_admit_another_call(study, monkeypatch, namespace):
    plan, _ = prepare(study)
    _, path = authorization(study, plan)
    run(study, plan, path)
    directory = grounding.grounding_directory(study['root'], plan)
    original = next((directory / 'units').glob('*.json'))
    destination = directory / namespace / original.name
    destination.parent.mkdir(exist_ok=True)
    original.rename(destination)
    from issueintel.runtime import io
    monkeypatch.setattr(io, 'load_local_env', lambda *args: pytest.fail('credentials opened for orphan inventory'))
    with pytest.raises(ValueError):
        run(study, plan, path, load_environment=True)
    assert len(study['calls']) == 4


@pytest.mark.parametrize('status', ['failed_bound', 'reserved', 'absent'])
@pytest.mark.parametrize('require_closed', [True, False])
def test_reviewed_unit_requires_returned_compatible_receipt_even_for_inspection(study, status, require_closed):
    plan, _ = prepare(study)
    _, path = authorization(study, plan)
    run(study, plan, path)
    record = deepcopy(next(iter(grounding._records(study['root'], plan).values())))
    unit = next(item for item in plan['units'] if item['unit_id'] == record['unit_id'])
    if status == 'absent':
        record['receipts'] = {}
    else:
        receipt = next(iter(record['receipts'].values()))
        receipt.update(status=status, observed_input_tokens=None, observed_output_tokens=None,
                       tokens_charged_or_reserved=receipt['input_bound'] + receipt['output_bound'])
    record = sealed({key: value for key, value in record.items() if key != 'sha256'})
    with pytest.raises(ValueError):
        grounding._validate_record(record, plan, unit, require_closed=require_closed)


def test_wire_schema_extra_fields_are_terminal_and_never_repaired(study):
    plan, _ = prepare(study)
    _, path = authorization(study, plan)
    base = study['provider']
    class ExtraField(base):
        def complete_structured(self, *args, **kwargs):
            value = super().complete_structured(*args, **kwargs)
            value['reviews'][0]['unrequested_field'] = 'not permitted by frozen wire schema'
            return value
    result = run(study, plan, path, provider=ExtraField)
    assert result['failed_units'] == 4 and result['completed_units'] == 0
    records = grounding._records(study['root'], plan)
    assert all(record['failure']['error_type'] == 'AnnotationError' for record in records.values())
    assert all(record['response']['reviews'][0]['unrequested_field'] for record in records.values())
    assert run(study, plan, path)['failed_units'] == 4 and len(study['calls']) == 4


def test_native_response_replay_also_rejects_unrequested_wire_fields(study):
    plan, _ = prepare(study)
    _, path = authorization(study, plan)
    run(study, plan, path)
    record = deepcopy(next(iter(grounding._records(study['root'], plan).values())))
    unit = next(item for item in plan['units'] if item['unit_id'] == record['unit_id'])
    record['response']['reviews'][0]['unrequested_field'] = 'not permitted'
    record['response_sha256'] = digest(record['response'])
    record['verification']['reviews'][0]['unrequested_field'] = 'not permitted'
    record = sealed({key: value for key, value in record.items() if key != 'sha256'})
    with pytest.raises(ValueError):
        grounding._validate_record(record, plan, unit)


def test_unit_time_is_counted_once_and_shared_arm_references_are_not_additive(study):
    plan, _ = prepare(study)
    _, path = authorization(study, plan)
    run(study, plan, path)
    records = deepcopy(grounding._records(study['root'], plan))
    for index, record in enumerate(records.values(), 1):
        record['latency_seconds'] = index
    report = grounding.summarize_grounding(plan, records)
    assert report['global_unique_unit_latency']['observed_units'] == 4
    assert report['global_unique_unit_latency']['observed_seconds'] == 10
    assert report['by_arm']['plus_history']['referenced_unit_latency'] == report['by_arm']['plus_verifier']['referenced_unit_latency']
    assert sum(row['referenced_unit_latency']['observed_seconds'] for row in report['by_arm'].values()) > 10
    assert all(row['reference_timing_is_additive_across_arms'] is False for row in report['by_arm'].values())
    assert report['by_arm']['plus_history']['judge_not_attempted_cases'] == 24
