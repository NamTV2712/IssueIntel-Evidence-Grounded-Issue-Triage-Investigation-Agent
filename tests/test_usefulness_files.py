"""Private offline review files and CLI, with synthetic selected exports only."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from issueintel.evaluation import usefulness_files as files
from issueintel.interfaces.cli import main
from test_usefulness import selected_inputs, encoded, reviewer_bytes, ratings_csv


def prepare(root):
    selection, inputs = selected_inputs()
    for name, raw in inputs.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    selection_path = root / 'artifacts/selection.json'
    selection_path.write_bytes(encoded(selection))
    output = root / 'artifacts/reviews/selected'
    return selection, inputs, selection_path, output


def built(root):
    selection, inputs, selection_path, output = prepare(root)
    result = files.build_files(root, selection_path, output)
    _, bundle = files._load(root, output)
    return selection, inputs, output, bundle, result


def rating_inputs(root, bundle, *, label='', reviewer_id='agent-one', **changes):
    csv_path = root / 'artifacts/submitted.csv'
    reviewer_path = root / 'artifacts/reviewer.json'
    csv_path.write_bytes(ratings_csv(bundle, label=label))
    reviewer_path.write_bytes(reviewer_bytes(reviewer_id=reviewer_id, **changes))
    return csv_path, reviewer_path


def test_build_freezes_private_sources_and_never_invents_ratings(tmp_path):
    selection, inputs, output, bundle, result = built(tmp_path)
    assert result['slots'] == 5 and result['ratings_created'] is False
    assert result['quality_evaluated'] is False
    reviewer_files = {p.name for p in (output / 'reviewer').iterdir()}
    assert reviewer_files == {'review.json', 'review.html', 'ratings.template.csv', 'ratings.csv'}
    display = (output / 'reviewer/review.json').read_text(encoding='utf-8')
    for private in ('C:\\private', 'local_receipt', 'condition_id', 'source_job_id', 'prompt_sha256', 'recommended_check'):
        assert private not in display
    for fingerprint in {ref['sha256'] for slot in selection['slots']
                        for ref in (slot['pack'], slot['artifact']) if ref}:
        assert (output / 'organizer/sources' / (fingerprint + '.json')).is_file()
    first = files.score_files(tmp_path, output)
    assert first['quality_status'] == 'unreviewed_no_nonblank_ratings'
    assert first['groups'] == [] and first['active_receipts'] == []
    assert sum(row['selected'] for row in first['coverage']) == 5
    # Freeze remains independent of original exports and their continued presence.
    for name in inputs:
        (tmp_path / name).unlink()
    assert files.score_files(tmp_path, output) == first
    assert files._load(tmp_path, output)[1]['display'] == bundle['display']


def test_editable_csv_import_is_append_only_and_correction_is_explicit(tmp_path):
    _, _, output, bundle, _ = built(tmp_path)
    csv_path, reviewer_path = rating_inputs(tmp_path, bundle)
    first = files.import_ratings(tmp_path, output, csv_path, reviewer_path)
    assert first['status'] == 'imported'
    receipt_folder = output / 'organizer/ratings'
    retained = {p.name: p.read_bytes() for p in receipt_folder.iterdir()}
    assert files.import_ratings(tmp_path, output, csv_path, reviewer_path)['status'] == 'already_present'
    # Editing the distributed copy is supported; the blank template stays frozen.
    csv_path = output / 'reviewer/ratings.csv'
    csv_path.write_bytes(ratings_csv(bundle, label='yes'))
    with pytest.raises(ValueError):
        files.import_ratings(tmp_path, output, csv_path, reviewer_path)
    assert {p.name: p.read_bytes() for p in receipt_folder.iterdir()} == retained
    corrected = files.import_ratings(tmp_path, output, csv_path, reviewer_path,
        supersedes=first['receipt_sha256'], correction_reason='Completed the previously blank synthetic ratings')
    assert corrected['status'] == 'imported' and len(list(receipt_folder.iterdir())) == 2
    for name, raw in retained.items():
        assert (receipt_folder / name).read_bytes() == raw
    report = files.score_files(tmp_path, output)
    assert report['quality_status'] == 'descriptive_declared_reviewer_ratings'
    assert len(report['active_receipts']) == 1
    assert report['active_receipts'][0]['receipt_sha256'] == corrected['receipt_sha256']
    assert report['independent_human_validation'] == 'not_established'
    assert report['causal_efficacy'] == 'not_measured'


@pytest.mark.parametrize('name', [
    'manifest.json', 'organizer/review.json', 'reviewer/review.json',
    'reviewer/review.html', 'reviewer/ratings.template.csv',
])
def test_changed_frozen_file_cannot_silently_change_the_review(tmp_path, name):
    _, _, output, _, _ = built(tmp_path)
    path = output / name
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises((ValueError, KeyError)):
        files.score_files(tmp_path, output)


def test_selected_raw_hash_failure_does_not_create_review_folder(tmp_path):
    _, inputs, selection_path, output = prepare(tmp_path)
    first = next(iter(inputs))
    (tmp_path / first).write_bytes(inputs[first] + b' ')
    with pytest.raises(ValueError, match='hash'):
        files.build_files(tmp_path, selection_path, output)
    assert not output.exists()


def test_output_refuses_overwrite_and_escape(tmp_path):
    _, _, output, _, _ = built(tmp_path)
    manifest = (output / 'manifest.json').read_bytes()
    with pytest.raises(ValueError):
        files.build_files(tmp_path, tmp_path / 'artifacts/selection.json', output)
    for candidate in (tmp_path / 'docs/review', tmp_path / 'data/review', tmp_path / '../review'):
        with pytest.raises(ValueError):
            files.build_files(tmp_path, tmp_path / 'artifacts/selection.json', candidate)
    assert (output / 'manifest.json').read_bytes() == manifest


def test_import_count_bound_keeps_exact_reimport_idempotent(tmp_path, monkeypatch):
    _, _, output, bundle, _ = built(tmp_path)
    csv_path, reviewer_path = rating_inputs(tmp_path, bundle)
    first = files.import_ratings(tmp_path, output, csv_path, reviewer_path)
    monkeypatch.setattr(files, 'MAX_RECEIPTS', 1)
    assert files.import_ratings(tmp_path, output, csv_path, reviewer_path)['status'] == 'already_present'
    csv_path, reviewer_path = rating_inputs(tmp_path, bundle, reviewer_id='agent-two')
    with pytest.raises(ValueError, match='bound'):
        files.import_ratings(tmp_path, output, csv_path, reviewer_path)
    assert len(list((output / 'organizer/ratings').glob('*.json'))) == 1
    assert files.score_files(tmp_path, output)['active_receipts'][0]['receipt_sha256'] == first['receipt_sha256']


def test_receipt_byte_bound_is_checked_before_publication(tmp_path, monkeypatch):
    _, _, output, bundle, _ = built(tmp_path)
    csv_path, reviewer_path = rating_inputs(tmp_path, bundle)
    files.import_ratings(tmp_path, output, csv_path, reviewer_path)
    folder = output / 'organizer/ratings'
    prior = {p.name: p.read_bytes() for p in folder.iterdir()}
    prior_size = sum(map(len, prior.values()))
    monkeypatch.setattr(files, 'MAX_RATING_BYTES', prior_size)
    csv_path, reviewer_path = rating_inputs(tmp_path, bundle, reviewer_id='agent-two')
    with pytest.raises(ValueError, match='bound'):
        files.import_ratings(tmp_path, output, csv_path, reviewer_path)
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == prior
    assert files.score_files(tmp_path, output)['quality_status'] == 'unreviewed_no_nonblank_ratings'


def test_second_writer_is_rejected_without_a_receipt(tmp_path):
    _, _, output, bundle, _ = built(tmp_path)
    csv_path, reviewer_path = rating_inputs(tmp_path, bundle)
    with files._writer(tmp_path, output):
        with pytest.raises(ValueError, match='writer'):
            files.import_ratings(tmp_path, output, csv_path, reviewer_path)
    assert not (output / 'organizer/ratings').exists()


def test_html_never_interprets_source_markup_or_fetches_links(tmp_path):
    _, _, output, bundle, _ = built(tmp_path)
    value = deepcopy(bundle['display'])
    value['slots'][0]['context']['issue'] = '<script>alert(1)</script><img src="https://private.invalid/x">'
    rendered = files._html(value).decode()
    assert '<script>' not in rendered and '<img' not in rendered
    assert '&lt;script&gt;' in rendered
    assert 'default-src &#39;none&#39;' in rendered and 'form-action &#39;none&#39;' in rendered


def test_cli_dispatches_before_environment_and_all_runtime_constructors(tmp_path, monkeypatch, capsys):
    _, _, selection_path, output = prepare(tmp_path)
    def deny(*args, **kwargs):
        pytest.fail('Offline review attempted environment/runtime/network/dataset initialization')
    for target in ('issueintel.interfaces.cli.load_local_env', 'issueintel.runtime.config.RuntimeConfig.load',
                   'issueintel.runtime.typesafe.TypesafeConfig.load', 'issueintel.investigation.workbench.Workbench.__init__',
                   'issueintel.integrations.github_sync.GitHubSync.__init__',
                   'issueintel.integrations.github_index.GitHubReader.get'):
        monkeypatch.setattr(target, deny)
    prefix = ['--root', str(tmp_path)]
    assert main(prefix + ['usefulness-build', '--selection', str(selection_path), '--output', str(output)]) == 0
    assert json.loads(capsys.readouterr().out)['ratings_created'] is False
    _, bundle = files._load(tmp_path, output)
    csv_path, reviewer_path = rating_inputs(tmp_path, bundle)
    assert main(prefix + ['usefulness-import', '--review', str(output), '--ratings', str(csv_path), '--reviewer', str(reviewer_path)]) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'imported'
    assert main(prefix + ['usefulness-score', '--review', str(output)]) == 0
    assert json.loads(capsys.readouterr().out)['quality_status'] == 'unreviewed_no_nonblank_ratings'
    assert not (tmp_path / 'data').exists() and not (tmp_path / '.env').exists()


def test_isolated_cli_needs_no_site_packages_or_environment_file(tmp_path):
    _, _, selection_path, output = prepare(tmp_path)
    source = Path(__file__).resolve().parents[1] / 'src'
    program = ('import sys;sys.path.insert(0,sys.argv.pop(1));'
        'import issueintel.interfaces.cli as cli;'
        'cli.load_local_env=lambda *_:(_ for _ in ()).throw(RuntimeError("environment read denied"));'
        'raise SystemExit(cli.main(sys.argv[1:]))')
    prefix = [sys.executable, '-I', '-S', '-c', program, str(source), '--root', str(tmp_path)]
    created = subprocess.run(prefix + ['usefulness-build', '--selection', str(selection_path), '--output', str(output)],
        cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert created.returncode == 0, created.stderr
    assert json.loads(created.stdout)['ratings_created'] is False
    scored = subprocess.run(prefix + ['usefulness-score', '--review', str(output)],
        cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert scored.returncode == 0, scored.stderr
    assert json.loads(scored.stdout)['quality_status'] == 'unreviewed_no_nonblank_ratings'
    assert not (tmp_path / 'data').exists() and not (tmp_path / '.venv').exists()


def test_score_export_is_new_private_json_and_never_overwrites(tmp_path):
    _, _, output, _, _ = built(tmp_path)
    target = tmp_path / 'artifacts/scores/report.json'
    report = files.score_files(tmp_path, output, target)
    assert json.loads(target.read_bytes()) == report
    original = target.read_bytes()
    with pytest.raises(FileExistsError):
        files.score_files(tmp_path, output, target)
    with pytest.raises(ValueError):
        files.score_files(tmp_path, output, tmp_path / 'docs/publish.json')
    assert target.read_bytes() == original


@pytest.mark.skipif(os.name != 'nt', reason='Windows junction guard')
def test_actual_windows_junction_input_is_not_traversed(tmp_path):
    _, inputs, selection_path, output = prepare(tmp_path)
    target = tmp_path / 'elsewhere'
    target.mkdir()
    (target / 'canary.json').write_text('do not change', encoding='utf-8')
    junction = tmp_path / 'artifacts/junction'
    subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command',
        'New-Item -ItemType Junction -Path $env:ISSUEINTEL_TEST_JUNCTION -Value $env:ISSUEINTEL_TEST_JUNCTION_TARGET | Out-Null'],
        env={**os.environ, 'ISSUEINTEL_TEST_JUNCTION': str(junction),
             'ISSUEINTEL_TEST_JUNCTION_TARGET': str(target)}, capture_output=True, check=True)
    try:
        with pytest.raises(ValueError, match='reparse'):
            files._read(tmp_path, junction / 'canary.json', artifacts=True)
        assert (target / 'canary.json').read_text(encoding='utf-8') == 'do not change'
    finally:
        assert junction.absolute().is_relative_to(tmp_path.absolute())
        os.rmdir(junction)  # Remove the named junction only; never recurse into its target.
