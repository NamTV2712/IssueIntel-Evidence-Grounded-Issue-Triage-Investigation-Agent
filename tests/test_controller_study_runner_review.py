"""Independent admission checks against mutation during lengthy native replay."""
import json
import sqlite3

import pytest

from issueintel.evaluation import controller_study_run as runner
from issueintel.runtime.study_budget import StudyBudgetStop
from test_controller_study_run import registered, save_authority
from test_controller_study import sources, planned_repository, save


@pytest.mark.parametrize('mutation', ['provider_approval', 'expired'])
def test_authority_changed_during_admission_denies_before_environment(registered, monkeypatch, mutation):
    root, spec, loader = registered
    path = save_authority(root, spec)
    original = runner._execution_views
    loaded_environment = []

    def changed(*args, **kwargs):
        views = original(*args, **kwargs)
        value = json.loads(path.read_bytes())
        if mutation == 'provider_approval':
            value['typesafe_spending_authorized'] = False
        else:
            value['expires_at'] = '2000-01-01T00:00:00+00:00'
        save(path, value)
        return views

    def environment(*args):
        loaded_environment.append(True)

    def provider(*args, **kwargs):
        pytest.fail('Revoked phase authority cannot construct providers')

    monkeypatch.setattr(runner, '_execution_views', changed)
    with pytest.raises((ValueError, OSError)):
        runner.run_study(root, spec['study_id'], path, limit=1,
            source_loader=loader, provider_factories={'groq': provider, 'typesafe': provider},
            load_environment=True, env_loader=environment)
    assert loaded_environment == []


def test_seal_rejects_pending_native_decision_even_when_calls_are_closed(monkeypatch):
    spec = {'study_id': 'a' * 64, 'sha256': 'b' * 64}
    results = {(str(index), 'rules'): {'sha256': str(index)} for index in range(10)}

    class Ledger:
        def inspect(self):
            return {'locked': False, 'calls': []}

    views = {'case-1': {'stages': [], 'calls': [], 'decisions': [{'status': 'pending'}]}}
    monkeypatch.setattr(runner, '_inventory', lambda *a: {})
    with pytest.raises(StudyBudgetStop):
        runner._seal_run(None, None, spec, Ledger(), results, views)


def test_unknown_paid_operation_can_be_inspected_but_not_resumed_before_credentials(registered):
    root, spec, loader = registered
    path = save_authority(root, spec)
    constructions = []

    def unavailable(*args, **kwargs):
        constructions.append(True)
        raise OSError('synthetic native client construction interruption')

    progress = runner.run_study(root, spec['study_id'], path, limit=1,
        source_loader=loader, provider_factories={'groq': unavailable})
    assert progress['stop_reason'] == 'source_storage_or_unknown_outcome_no_automatic_retry'
    assert progress['owned_calls'] == 1 and progress['inference_sealed'] is False
    assert constructions == [True]
    inspection = runner.inspect_run(root, spec['study_id'], source_loader=loader)
    assert inspection['interrupted_work'] is True
    assert inspection['owned_calls'] == 1 and inspection['provider_constructed'] is False
    loaded_environment = []
    with pytest.raises(StudyBudgetStop, match='Interrupted or unknown'):
        runner.run_study(root, spec['study_id'], path, limit=1,
            source_loader=loader, provider_factories={'groq': unavailable},
            load_environment=True, env_loader=lambda *a: loaded_environment.append(True))
    assert loaded_environment == [] and constructions == [True]


def test_matrix_authority_cannot_bypass_pilot_gate_by_requesting_one_case(registered):
    root, spec, loader = registered
    path = save_authority(root, spec, matrix=True)
    with pytest.raises(ValueError, match='phase'):
        runner.run_study(root, spec['study_id'], path, limit=1, source_loader=loader,
            load_environment=True, env_loader=lambda *a: pytest.fail('Matrix cannot stand in for pilot authority'))


def test_orphan_native_ledger_without_unit_journals_denies_before_environment(registered):
    root, spec, loader = registered
    path = save_authority(root, spec)
    directory = runner.registry.study_directory(root, spec['study_id'])
    native = directory / 'native/groq.sqlite'
    native.parent.mkdir()
    with sqlite3.connect(native) as db:
        db.execute('CREATE TABLE calls(id TEXT PRIMARY KEY)')
        db.execute('INSERT INTO calls VALUES(?)', ('orphan-reservation',))
    with pytest.raises(StudyBudgetStop, match='lost its joint ledger'):
        runner.inspect_run(root, spec['study_id'], source_loader=loader)
    with pytest.raises(StudyBudgetStop, match='orphan'):
        runner.run_study(root, spec['study_id'], path, source_loader=loader,
            load_environment=True, env_loader=lambda *a: pytest.fail('Orphan native receipt before credentials'))
