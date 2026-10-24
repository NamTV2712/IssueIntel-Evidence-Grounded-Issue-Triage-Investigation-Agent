"""Paired CLI uses the native file adapter without environment or model setup."""
import json

import pytest

from issueintel.interfaces import cli
from test_usefulness import reviewer_bytes
from test_usefulness_pair_files import prepare


def no_environment(*args, **kwargs):
    raise AssertionError('Offline paired review reached environment loading')


def test_complete_unrated_cli_flow_keeps_provenance_and_never_fabricates_labels(tmp_path, monkeypatch, capsys):
    _, _, selection, output = prepare(tmp_path)
    monkeypatch.setattr(cli, 'load_local_env', no_environment)
    assert cli.main(['--root', str(tmp_path), 'usefulness-pair-build',
        '--selection', str(selection), '--output', str(output)]) == 0
    built = json.loads(capsys.readouterr().out)
    assert built['ratings_created'] is False and built['case_clusters'] == 4
    reviewer = tmp_path / 'artifacts/reviewer.json'
    reviewer.write_bytes(reviewer_bytes())
    assert cli.main(['--root', str(tmp_path), 'usefulness-pair-import', '--review', str(output),
        '--ratings', str(output / 'reviewer/ratings.csv'), '--reviewer', str(reviewer)]) == 0
    imported = json.loads(capsys.readouterr().out)
    assert imported['status'] == 'imported' and imported['independence_verified'] is False
    assert cli.main(['--root', str(tmp_path), 'usefulness-pair-score', '--review', str(output)]) == 0
    scored = json.loads(capsys.readouterr().out)
    assert scored['quality_status'] == 'unreviewed_no_nonblank_ratings'
    assert scored['independent_human_validation'] == 'not_established'
    assert scored['causal_efficacy'] == 'not_measured'


@pytest.mark.parametrize('command,arguments', [
    ('usefulness-pair-build', ['--selection', 'artifacts/missing.json', '--output', 'artifacts/review']),
    ('usefulness-pair-import', ['--review', 'artifacts/missing', '--ratings', 'artifacts/missing.csv',
                               '--reviewer', 'artifacts/reviewer.json']),
    ('usefulness-pair-score', ['--review', 'artifacts/missing']),
])
def test_invalid_inputs_fail_before_environment_with_fixed_error(tmp_path, monkeypatch, capsys, command, arguments):
    monkeypatch.setattr(cli, 'load_local_env', no_environment)
    assert cli.main(['--root', str(tmp_path), command, *arguments]) == 2
    error = capsys.readouterr().err
    assert 'paired review' in error and str(tmp_path) not in error


def test_paired_build_does_not_route_through_v1(tmp_path, monkeypatch, capsys):
    from issueintel.evaluation import usefulness_files
    monkeypatch.setattr(usefulness_files, 'build_files', no_environment)
    monkeypatch.setattr(cli, 'load_local_env', no_environment)
    _, _, selection, output = prepare(tmp_path)
    assert cli.main(['--root', str(tmp_path), 'usefulness-pair-build',
        '--selection', str(selection), '--output', str(output)]) == 0
    assert json.loads(capsys.readouterr().out)['pairs'] == 5
