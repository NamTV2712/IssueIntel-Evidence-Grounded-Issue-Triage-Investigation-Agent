"""Controller study commands defer credentials and keep private rows off stdout."""
import json
from pathlib import Path

import pytest

from issueintel.interfaces import cli
from issueintel.evaluation import controller_study as registration
from issueintel.evaluation import controller_study_run as runner


def denied(*args, **kwargs):
    pytest.fail('Dispatch must not load credentials or instantiate a provider')


@pytest.mark.parametrize('mode', ['register', 'inspect', 'score', 'run'])
def test_study_dispatch_keeps_authority_and_credentials_at_runner(tmp_path, monkeypatch, capsys, mode):
    monkeypatch.setattr(cli, 'load_local_env', denied)
    monkeypatch.setattr(runner, 'load_local_env', denied)
    seen = []
    def selected(root, selection):
        seen.append((root, selection))
        return {'schema': 'controller_study_registration_v1', 'study_id': 'a' * 64,
            'sha256': 'b' * 64, 'cases': [{'private_source': 'never print this'}] * 5}
    def call(root, study, *args, **kwargs):
        seen.append((root, study, args, kwargs))
        return {'study_id': study, 'provider_constructed': False}
    monkeypatch.setattr(registration, 'register_study', selected)
    monkeypatch.setattr(runner, 'inspect_run', call)
    monkeypatch.setattr(runner, 'score_study', call)
    monkeypatch.setattr(runner, 'run_study', call)
    args = ['--root', str(tmp_path), 'controller-study-' + mode]
    args += ['--selection', 'artifacts/selection.json'] if mode == 'register' else ['--study', 'a' * 64]
    if mode == 'run':
        args += ['--authorization', 'artifacts/separate authority.json', '--limit', '5']
    assert cli.main(args) == 0
    output = capsys.readouterr().out
    assert 'never print this' not in output
    assert json.loads(output)['study_id'] == 'a' * 64
    assert seen[0][0] == tmp_path.resolve()
    if mode == 'run':
        assert seen[0][2] == (Path('artifacts/separate authority.json'),)
        assert seen[0][3] == {'limit': 5, 'load_environment': True}


def test_study_rejection_is_sanitized_and_does_not_fall_through(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, 'load_local_env', denied)
    def reject(*a, **kw):
        raise OSError('private issue body and transport details')
    monkeypatch.setattr(runner, 'inspect_run', reject)
    assert cli.main(['--root', str(tmp_path), 'controller-study-inspect', '--study', 'a' * 64]) == 2
    printed = capsys.readouterr()
    assert not printed.out
    assert 'private issue' not in printed.err
    assert 'no automatic retry' in printed.err


def test_controller_scope_only_pilot_or_five_before_any_credentials(monkeypatch):
    monkeypatch.setattr(cli, 'load_local_env', denied)
    monkeypatch.setattr(runner, 'run_study', denied)
    with pytest.raises(SystemExit) as error:
        cli.main(['controller-study-run', '--study', 'a' * 64,
            '--authorization', 'artifacts/authority.json', '--limit', '100'])
    assert error.value.code == 2
