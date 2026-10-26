"""Independent crash-boundary checks for the native controller study adapter."""
from copy import deepcopy

import pytest

from issueintel.evaluation import controller_study_execution as implementation
from issueintel.investigation.product_study import stage_binding
from issueintel.runtime.io import digest
from issueintel.runtime.study_budget import StudyBudgetStop
from test_controller_study_execution import owned, stage, run, MESSAGES
from test_planned_pipeline import planned_repository


def test_joint_reservation_before_missing_call_event_cannot_restart(owned, monkeypatch):
    execution = owned['create']()
    original = execution.journal.append

    def crash(kind, data):
        if kind == 'call_started':
            raise OSError('synthetic write interrupted after joint reservation')
        return original(kind, data)

    monkeypatch.setattr(execution.journal, 'append', crash)
    with pytest.raises(OSError):
        stage(owned, execution, lambda: execution.groq(MESSAGES, 4096))
    snapshot = owned['budget'].inspect()
    assert snapshot['owned_attempts'] == 1
    assert snapshot['calls'][0]['outcome'] == 'pending'
    assert snapshot['owned_micro_usd_charged_or_reserved'] > 0
    assert owned['constructors'] == owned['requests'] == []
    with pytest.raises(StudyBudgetStop, match='orphan'):
        owned['create']('jev')


def test_completed_joint_call_without_completed_execution_event_is_not_reconstructed(owned, monkeypatch):
    execution = owned['create']()
    original = execution.journal.append

    def crash(kind, data):
        if kind == 'call_finished' and data['status'] == 'completed':
            raise OSError('synthetic crash after joint finish')
        return original(kind, data)

    monkeypatch.setattr(execution.journal, 'append', crash)
    with pytest.raises(StudyBudgetStop, match='closed before journal completion'):
        stage(owned, execution, lambda: {'response': execution.groq(MESSAGES, 4096)})
    snapshot = owned['budget'].inspect()
    before = deepcopy(snapshot)
    assert snapshot['calls'][0]['outcome'] == 'completed'
    assert snapshot['calls'][0]['receipt']['status'] == 'settled'
    assert len(owned['requests']) == 1
    with pytest.raises(StudyBudgetStop, match='differs from joint budget'):
        owned['create']('jev')
    assert owned['budget'].inspect() == before
    assert len(owned['requests']) == 1


def test_typesafe_terminal_publication_gap_cannot_become_rules_fallback(owned, monkeypatch):
    execution = owned['create']('jev')
    original = execution.journal.append
    failed = []

    def crash(kind, data):
        if (kind == 'receipt' and data['receipt'].get('provider') == 'typesafe'
                and data['receipt']['status'] == 'settled' and not failed):
            failed.append(True)
            raise OSError('synthetic gap after terminal joint receipt')
        return original(kind, data)

    monkeypatch.setattr(execution.journal, 'append', crash)
    with pytest.raises(StudyBudgetStop):
        run(owned, execution, jev=True)
    assert failed
    rows = [row for row in owned['budget'].inspect()['calls'] if row['provider'] == 'typesafe']
    assert len(rows) == 1 and rows[0]['outcome'] == 'failed'
    assert rows[0]['receipt']['status'] == 'settled'
    assert execution.typesafe_client.last_receipt == rows[0]['receipt']
    assert len([row for row in owned['requests'] if row[0] == 'typesafe']) == 1
    view = execution.inspect()
    assert len(view['decisions']) == 1
    assert view['decisions'][0]['status'] == 'pending'
    with pytest.raises(StudyBudgetStop, match='Interrupted'):
        owned['create']('rules')


def test_raw_response_orphan_after_fsync_blocks_reopen_and_retains_usage(owned, monkeypatch):
    execution = owned['create']()
    original = implementation._write
    failed = []

    def crash(path, raw):
        original(path, raw)
        if path.parent == execution.journal.raw_dir and not failed:
            failed.append(True)
            raise OSError('synthetic gap before raw event')

    monkeypatch.setattr(implementation, '_write', crash)
    with pytest.raises(StudyBudgetStop):
        stage(owned, execution, lambda: execution.groq(MESSAGES, 4096))
    assert failed
    snapshot = owned['budget'].inspect()
    assert snapshot['owned_attempts'] == 1
    assert snapshot['calls'][0]['receipt']['status'] == 'failed_bound'
    assert snapshot['owned_micro_usd_charged_or_reserved'] == snapshot['calls'][0]['micro_usd_bound']
    assert len(owned['requests']) == 1
    with pytest.raises(StudyBudgetStop, match='Unowned study raw'):
        owned['create']('jev')


def test_shared_cost_reference_gap_recovers_only_metadata_and_never_repeats_call(owned, monkeypatch):
    first = owned['create']()
    payload = stage(owned, first, lambda: {'response': first.groq(MESSAGES, 4096)})
    second = owned['create']('jev')
    original = second.journal.append
    failed = []

    def crash(kind, data):
        if kind == 'stage_reference' and not failed:
            failed.append(True)
            raise OSError('synthetic gap after exact shared cost reference')
        return original(kind, data)

    monkeypatch.setattr(second.journal, 'append', crash)
    with pytest.raises(OSError):
        stage(owned, second, lambda: pytest.fail('shared stage cannot execute'))
    before = deepcopy(owned['budget'].inspect())
    assert len(before['calls'][0]['references']) == 2
    reopened = owned['create']('jev')
    assert stage(owned, reopened, lambda: pytest.fail('shared stage cannot execute')) == payload
    assert len(owned['requests']) == 1
    assert owned['budget'].inspect() == before
    assert reopened.inspect()['stages'][0]['references'] == ['case-01:rules', 'case-01:jev']


@pytest.mark.parametrize('interrupted', ['pending_stage', 'unknown_call'])
def test_inspection_only_can_report_consistent_interruption_without_mutating_work(owned, interrupted):
    execution = owned['create']()
    if interrupted == 'pending_stage':
        binding = stage_binding(owned['source'][-1], 'seed_generation', {'native': 'fixed'})
        execution.journal.append('stage_started', {'key': digest(binding),
            'kind': 'seed_generation', 'binding': binding, 'owner_arm': 'case-01:rules'})
    else:
        def unavailable(*args, **kwargs):
            raise OSError('synthetic constructor failure before native reservation')
        execution.provider_factories['groq'] = unavailable
        with pytest.raises(StudyBudgetStop):
            stage(owned, execution, lambda: execution.groq(MESSAGES, 4096))
    with pytest.raises(StudyBudgetStop, match='Interrupted'):
        owned['create']('jev')
    inspection = owned['create']('jev', inspection_only=True)
    before = inspection.inspect()
    budget_before = owned['budget'].inspect()
    if interrupted == 'pending_stage':
        assert before['stages'][0]['status'] == 'pending'
    else:
        assert before['calls'][0]['status'] == 'unknown'
    with pytest.raises(StudyBudgetStop, match='Inspection-only'):
        inspection._call(None, None, None, None, None, None, None)
    with pytest.raises(StudyBudgetStop, match='Inspection-only'):
        inspection.stage('seed_generation', {}, lambda: pytest.fail('Inspection cannot execute a stage'))
    with pytest.raises(StudyBudgetStop, match='Inspection-only'):
        inspection.on_decision({})
    assert inspection.inspect() == before
    assert owned['budget'].inspect() == budget_before
    assert owned['constructors'] == owned['requests'] == []


def test_inspection_only_still_rejects_joint_execution_status_mismatch(owned, monkeypatch):
    execution = owned['create']()
    original = execution.journal.append

    def incomplete(kind, data):
        if kind == 'call_finished' and data['status'] == 'completed':
            raise OSError('synthetic journal completion gap')
        return original(kind, data)

    monkeypatch.setattr(execution.journal, 'append', incomplete)
    with pytest.raises(StudyBudgetStop):
        stage(owned, execution, lambda: {'response': execution.groq(MESSAGES, 4096)})
    with pytest.raises(StudyBudgetStop, match='differs from joint budget'):
        owned['create']('jev', inspection_only=True)
    assert len(owned['requests']) == 1


@pytest.mark.parametrize('invalid', [None, 0, 1, 'true'])
def test_inspection_mode_requires_an_explicit_boolean(owned, invalid):
    with pytest.raises(StudyBudgetStop):
        owned['create'](inspection_only=invalid)
    assert owned['budget'].inspect()['owned_attempts'] == 0
    assert owned['constructors'] == owned['requests'] == []
