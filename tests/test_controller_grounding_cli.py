"""The common product critic never imports a changing implementation package."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from issueintel.interfaces import cli


def denied(*args, **kwargs):
    raise AssertionError('Dispatcher cannot read credentials')


@pytest.mark.parametrize('mode,arguments', [
    ('preview', ['--parent-study', 'a' * 64]),
    ('run', ['--study', 'b' * 64, '--authorization', 'artifacts/quality approval.json', '--limit', '5']),
    ('inspect', ['--study', 'b' * 64]),
    ('score', ['--study', 'b' * 64]),
])
def test_dispatch_explicit_files_and_defer_credentials(tmp_path, monkeypatch, mode, arguments):
    monkeypatch.setattr(cli, 'load_local_env', denied)
    paths = tmp_path / 'adapters/evaluation'; paths.mkdir(parents=True)
    for name in ('common_grounding', 'controller_grounding'):
        (paths / (name + '.py')).write_text('# synthetic adapter\n')
    monkeypatch.setattr(cli, '__file__', str(tmp_path / 'adapters/interfaces/cli.py'))
    seen = []
    def child(command, *, check):
        seen.append(command)
        assert check is False
        return SimpleNamespace(returncode=7)
    monkeypatch.setattr(cli.subprocess, 'run', child)
    assert cli.main(['--root', str(tmp_path), 'controller-grounding-' + mode, *arguments]) == 7
    command = seen[0]
    assert command[1:3] == ['-I', '-c']
    assert command[4] == str(tmp_path.resolve())
    assert [Path(p).name for p in command[5:7]] == ['common_grounding.py', 'controller_grounding.py']
    assert command[7:11] == ['--root', str(tmp_path.resolve()), '--mode', mode]
    expected = list(arguments)
    if '--authorization' in expected:
        index = expected.index('--authorization') + 1
        expected[index] = str(Path(expected[index]))
    assert command[11:] == expected


def test_isolated_child_uses_frozen_native_and_exact_helper_only(tmp_path, monkeypatch, capfd):
    frozen = tmp_path / 'frozen'
    package = frozen / 'src/issueintel'
    (package / 'evaluation').mkdir(parents=True)
    (package / '__init__.py').write_text('ORIGIN="frozen"\n')
    (package / 'evaluation/__init__.py').write_text('')
    external = tmp_path / 'external'
    (external / 'evaluation').mkdir(parents=True)
    (external / 'evaluation/common_grounding.py').write_text('VALUE="exact helper"\n')
    (external / 'evaluation/controller_grounding.py').write_text(
        'from .common_grounding import VALUE\n'
        'import issueintel,json\n'
        'assert issueintel.ORIGIN=="frozen" and VALUE=="exact helper"\n'
        'def main(argv):\n print(json.dumps(argv)); return 0\n')
    (external / 'evaluation/json.py').write_text('raise RuntimeError("neighbor shadow")\n')
    poison = tmp_path / 'poison/issueintel'; poison.mkdir(parents=True)
    (poison / '__init__.py').write_text('raise RuntimeError("wrong package")\n')
    monkeypatch.setenv('PYTHONPATH', str(poison.parent))
    monkeypatch.setattr(cli, '__file__', str(external / 'interfaces/cli.py'))
    monkeypatch.setattr(cli, 'load_local_env', denied)
    assert cli.main(['--root', str(frozen), 'controller-grounding-inspect', '--study', 'b' * 64]) == 0
    assert json.loads(capfd.readouterr().out) == ['--root', str(frozen.resolve()), '--mode', 'inspect', '--study', 'b' * 64]


def test_no_expanded_prefix_before_child_or_credentials(monkeypatch):
    monkeypatch.setattr(cli, 'load_local_env', denied)
    monkeypatch.setattr(cli.subprocess, 'run', denied)
    with pytest.raises(SystemExit) as caught:
        cli.main(['controller-grounding-run', '--study', 'b' * 64,
            '--authorization', 'artifacts/approval.json', '--limit', '100'])
    assert caught.value.code == 2
