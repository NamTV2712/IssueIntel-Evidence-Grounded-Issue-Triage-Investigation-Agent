"""The common judge runs independently of its frozen inference package."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from issueintel.interfaces import cli


def denied(*args, **kwargs):
    raise AssertionError('Parent dispatcher must not load credentials')


@pytest.mark.parametrize('mode,arguments', [
    ('preview', ['--parent-plan', 'a' * 64]),
    ('run', ['--study', 'b' * 64, '--authorization', 'artifacts/study decision.json', '--limit', '25']),
    ('inspect', ['--study', 'b' * 64]),
    ('score', ['--study', 'b' * 64]),
])
def test_dispatch_preserves_arguments_and_defers_credentials(tmp_path, monkeypatch, mode, arguments):
    monkeypatch.setattr(cli, 'load_local_env', denied)
    captured = []
    def child(values, *, check):
        captured.append(values)
        assert check is False
        return SimpleNamespace(returncode=9)
    monkeypatch.setattr(cli.subprocess, 'run', child)
    assert cli.main(['--root', str(tmp_path), 'component-grounding-' + mode, *arguments]) == 9
    values = captured[0]
    assert values[1:3] == ['-I', '-c']
    assert values[4] == str(tmp_path.resolve())
    assert Path(values[5]).name == 'component_grounding.py'
    assert values[6:10] == ['--root', str(tmp_path.resolve()), '--mode', mode]
    expected = list(arguments)
    if '--authorization' in expected:
        index = expected.index('--authorization') + 1
        expected[index] = str(Path(expected[index]))
    assert values[10:] == expected


def test_grounding_child_cannot_import_other_checkout_or_adapter_neighbors(tmp_path, monkeypatch, capfd):
    frozen = tmp_path / 'frozen root'
    (frozen / 'src/issueintel').mkdir(parents=True)
    (frozen / 'src/issueintel/__init__.py').write_text('ORIGIN="registered"\n', encoding='utf-8')
    adapter = tmp_path / 'external adapter'
    (adapter / 'benchmarks').mkdir(parents=True)
    (adapter / 'benchmarks/component_grounding.py').write_text(
        'import issueintel,json,sys\n'
        'assert issueintel.ORIGIN=="registered"\n'
        'print(json.dumps(sys.argv[1:]))\n', encoding='utf-8')
    (adapter / 'benchmarks/json.py').write_text('raise RuntimeError("Unexpected adapter import")\n')
    poison = tmp_path / 'unrelated checkout'
    (poison / 'issueintel').mkdir(parents=True)
    (poison / 'issueintel/__init__.py').write_text('raise RuntimeError("Unexpected caller import")\n')
    monkeypatch.setenv('PYTHONPATH', str(poison))
    monkeypatch.setattr(cli, '__file__', str(adapter / 'interfaces/cli.py'))
    monkeypatch.setattr(cli, 'load_local_env', denied)
    assert cli.main(['--root', str(frozen), 'component-grounding-score', '--study', 'b' * 64]) == 0
    assert json.loads(capfd.readouterr().out) == ['--root', str(frozen.resolve()), '--mode', 'score', '--study', 'b' * 64]


def test_matrix_prefix_must_be_pilot_or_full_before_child(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, 'load_local_env', denied)
    monkeypatch.setattr(cli.subprocess, 'run', denied)
    with pytest.raises(SystemExit) as caught:
        cli.main(['--root', str(tmp_path), 'component-grounding-run', '--study', 'b' * 64,
            '--authorization', 'artifacts/study.json', '--limit', '400'])
    assert caught.value.code == 2
