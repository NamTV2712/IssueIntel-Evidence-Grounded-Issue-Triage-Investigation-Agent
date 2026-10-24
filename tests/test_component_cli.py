"""CLI boundaries: offline commands never load secrets; execution checks authority first."""
import sys
from types import ModuleType

import pytest

from issueintel.interfaces import cli
from issueintel.benchmarks import component_ablation as component


def forbidden(*args, **kwargs):
    raise AssertionError('Offline component command reached credential loading')


@pytest.mark.parametrize('command,function,arguments', [
    ('component-register', 'register_component_ablation', []),
    ('component-preflight', 'preflight_component_ablation', ['--experiment', 'a' * 64]),
    ('component-inspect', 'inspect_component_ablation', ['--experiment', 'a' * 64]),
])
def test_source_commands_do_not_load_environment(tmp_path, monkeypatch, capsys, command, function, arguments):
    monkeypatch.setattr(cli, 'load_local_env', forbidden)
    monkeypatch.setattr(component, function, lambda *a, **kw: {'gold_read': False, 'provider_calls': 0})
    if command == 'component-inspect':
        from issueintel.benchmarks import component_ablation_run
        monkeypatch.setattr(component_ablation_run, 'inspect_component_run', lambda *a: {'state': 'not_started'})
    assert cli.main(['--root', str(tmp_path), command, *arguments]) == 0
    assert '"provider_calls": 0' in capsys.readouterr().out


def test_separate_scoring_does_not_load_environment(tmp_path, monkeypatch):
    module = ModuleType('issueintel.benchmarks.component_ablation_score')
    module.score_component_ablation = lambda *a: {'status': 'scored'}
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(cli, 'load_local_env', forbidden)
    assert cli.main(['--root', str(tmp_path), 'component-score', '--experiment', 'a' * 64,
                     '--authorization', 'artifacts/authorization.json']) == 0


def test_cached_history_preparation_does_not_load_environment(tmp_path, monkeypatch):
    module = ModuleType('issueintel.benchmarks.component_history')
    module.prepare_component_history = lambda *a: {'provider_calls': 0, 'gold_read': False}
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(cli, 'load_local_env', forbidden)
    assert cli.main(['--root', str(tmp_path), 'component-history', '--output',
                     'artifacts/history.json']) == 0


@pytest.mark.parametrize('failure', ['registration', 'authorization'])
def test_denied_execution_never_reads_credentials(tmp_path, monkeypatch, capsys, failure):
    monkeypatch.setattr(cli, 'load_local_env', forbidden)
    def reject(*args, **kwargs):
        raise ValueError('Sensitive local details are not printed')
    monkeypatch.setattr(component, 'load_registration', reject if failure == 'registration' else lambda *a: ({}, tmp_path))
    monkeypatch.setattr(component, 'verify_run_authorization', reject)
    assert cli.main(['--root', str(tmp_path), 'component-run', '--experiment', 'a' * 64,
                     '--authorization', 'artifacts/authorization.json']) == 2
    assert 'Sensitive local' not in capsys.readouterr().err


def test_execution_loads_credentials_only_after_matching_authority(tmp_path, monkeypatch):
    order = []
    monkeypatch.setattr(component, 'load_registration', lambda *a: (order.append('registration') or {}, tmp_path))
    monkeypatch.setattr(component, 'verify_run_authorization', lambda *a, **kw: order.append('authorization'))
    monkeypatch.setattr(cli, 'load_local_env', lambda *a: order.append('credentials'))
    module = ModuleType('issueintel.benchmarks.component_ablation_run')
    module.run_component_ablation = lambda *a, **kw: order.append('runner') or {'status': 'pilot_complete'}
    monkeypatch.setitem(sys.modules, module.__name__, module)
    assert cli.main(['--root', str(tmp_path), 'component-run', '--experiment', 'a' * 64,
                     '--authorization', 'artifacts/authorization.json']) == 0
    assert order == ['registration', 'authorization', 'credentials', 'runner']


def test_execution_does_not_accept_arbitrary_case_count(tmp_path):
    with pytest.raises(SystemExit) as error:
        cli.main(['--root', str(tmp_path), 'component-run', '--experiment', 'a' * 64,
                  '--authorization', 'artifacts/authorization.json', '--limit', '2'])
    assert error.value.code == 2
