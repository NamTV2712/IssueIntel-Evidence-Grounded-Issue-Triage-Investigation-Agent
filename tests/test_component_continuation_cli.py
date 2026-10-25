"""The new adapter must not replace the experiment's pinned inference imports."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from issueintel.interfaces import cli


def no_environment(*args, **kwargs):
    raise AssertionError('Parent CLI must not load credentials')


@pytest.mark.parametrize('mode,extra', [
    ('preview', ['--experiment', 'a' * 64]),
    ('inspect', ['--plan', 'b' * 64]),
    ('run', ['--plan', 'b' * 64, '--authorization', 'artifacts/decision with spaces.json', '--limit', '1']),
    ('score', ['--plan', 'b' * 64, '--authorization', 'artifacts/score.json']),
])
def test_parent_delegates_separate_arguments_without_environment(tmp_path, monkeypatch, mode, extra):
    monkeypatch.setattr(cli, 'load_local_env', no_environment)
    seen = []
    def child(arguments, *, check):
        seen.append(arguments)
        assert check is False
        return SimpleNamespace(returncode=7)
    monkeypatch.setattr(cli.subprocess, 'run', child)
    root = tmp_path / 'frozen runtime with spaces'
    assert cli.main(['--root', str(root), 'component-continuation-' + mode, *extra]) == 7
    arguments = seen[0]
    assert arguments[1:3] == ['-I', '-c']
    assert arguments[4] == str(root.resolve())
    assert Path(arguments[5]).name == 'component_continuation_run.py'
    assert arguments[6:10] == ['--root', str(root.resolve()), '--mode', mode]
    expected = list(extra)
    if '--authorization' in expected:
        index = expected.index('--authorization') + 1
        expected[index] = str(Path(expected[index]))
    assert arguments[10:] == expected


def test_isolated_child_uses_frozen_package_and_ignores_adapter_folder_and_pythonpath(tmp_path, monkeypatch, capfd):
    frozen = tmp_path / 'runtime with spaces'
    package = frozen / 'src/issueintel'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text("ORIGIN = 'frozen'\n", encoding='utf-8')
    adapter = tmp_path / 'adapter'
    driver_folder = adapter / 'benchmarks'
    driver_folder.mkdir(parents=True)
    (driver_folder / 'component_continuation_run.py').write_text(
        'import issueintel,json,sys\n'
        'assert issueintel.ORIGIN == "frozen"\n'
        'print(json.dumps({"origin": issueintel.ORIGIN, "argv": sys.argv[1:]}))\n', encoding='utf-8')
    (driver_folder / 'json.py').write_text("raise RuntimeError('Adapter folder shadowed the standard library')\n")
    poison = tmp_path / 'caller-pythonpath'
    (poison / 'issueintel').mkdir(parents=True)
    (poison / 'issueintel/__init__.py').write_text("raise RuntimeError('Caller PYTHONPATH was imported')\n")
    monkeypatch.setenv('PYTHONPATH', str(poison))
    monkeypatch.setattr(cli, '__file__', str(adapter / 'interfaces/cli.py'))
    monkeypatch.setattr(cli, 'load_local_env', no_environment)
    assert cli.main(['--root', str(frozen), 'component-continuation-preview', '--experiment', 'a' * 64]) == 0
    result = json.loads(capfd.readouterr().out)
    assert result['origin'] == 'frozen'
    assert result['argv'] == ['--root', str(frozen.resolve()), '--mode', 'preview', '--experiment', 'a' * 64]


def test_invalid_prefix_size_is_rejected_without_child_or_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, 'load_local_env', no_environment)
    monkeypatch.setattr(cli.subprocess, 'run', no_environment)
    with pytest.raises(SystemExit) as caught:
        cli.main(['--root', str(tmp_path), 'component-continuation-run', '--plan', 'b' * 64,
                  '--authorization', 'artifacts/a.json', '--limit', '400'])
    assert caught.value.code == 2
