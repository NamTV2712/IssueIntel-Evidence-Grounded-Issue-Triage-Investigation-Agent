"""Bounded offline paired review I/O, using only synthetic product exports."""
from copy import deepcopy
import csv
import io
import json
import os
import subprocess

import pytest

from issueintel.evaluation import usefulness_pair_files as files
from issueintel.runtime.io import digest
from test_usefulness import DIMENSIONS, encoded, raw_sha, reviewer_bytes, product_pack
from test_usefulness_pairs import selected_inputs as paired_selected_inputs


def pair_inputs():
    """Four source jobs; only the first contributes both proposal kinds."""
    pairs, inputs = [], {}
    for index, (unit, kinds, status) in enumerate((
        ('one', ('question', 'check'), 'completed'),
        ('two', ('check',), 'empty'),
        ('three', ('question',), 'failed'),
        ('four', ('check',), 'unavailable'),
    ), 1):
        pack = product_pack()
        source_job_id = f'{index:024x}'
        pack['run_id'] = source_job_id
        pack['case']['case_id'] = pack['annotation']['case_id'] = 'local:synthetic-' + unit
        query = pack['case']['problem_statement'] + ' Synthetic source ' + unit + '.'
        pack['case']['problem_statement'] = query
        pack['evidence'][0].update(text=query, sha256=raw_sha(query.encode('utf-8')))
        selected, raw_sources = paired_selected_inputs(
            native_status=status, pack=pack, source_job_id=source_job_id,
            unit_id=unit, path_prefix='artifacts/synthetic/' + unit)
        for pair in selected['pairs']:
            if pair['kind'] not in kinds:
                continue
            for reference in (pair['pack'], *(v['artifact'] for v in pair['variants'])):
                if reference is not None:
                    inputs[reference['path']] = raw_sources[reference['path']]
            pairs.append(pair)
    return {'schema': 'usefulness_pair_selection_v2', 'purpose': 'Synthetic paired offline review',
            'protocol_id': 'synthetic-pairs-v2', 'language': 'en', 'pairs': pairs}, inputs


def prepare(root):
    selection, inputs = pair_inputs()
    for name, raw in inputs.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    path = root / 'artifacts/selection.json'
    path.write_bytes(encoded(selection))
    return selection, inputs, path, root / 'artifacts/reviews/paired'


def built(root):
    selection, inputs, path, output = prepare(root)
    result = files.build_pair_files(root, path, output)
    _, bundle = files._load(root, output)
    return selection, inputs, output, bundle, result


def ratings(bundle, *, label='', preference='', overrides=None):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=files.core.RATING_FIELDS)
    writer.writeheader()
    for card in bundle['display']['pairs']:
        row = {'blind_id': card['blind_id'], 'display_sha256': digest(card)}
        for side in ('A', 'B'):
            if card['alternatives'][side]['status'] == 'completed':
                row.update({side + '_' + field: label for field in DIMENSIONS})
                row[side + '_rationale'] = 'Synthetic declared reasoning' if label else ''
        if all(card['alternatives'][side]['status'] == 'completed' for side in ('A', 'B')):
            row['preference'] = preference
            row['preference_rationale'] = 'Synthetic comparison' if preference else ''
        row.update((overrides or {}).get(card['blind_id'], {}))
        writer.writerow(row)
    return stream.getvalue().encode('utf-8')


def submitted(root, bundle, *, label='', preference='', reviewer_id='agent-one', **reviewer):
    csv_path = root / 'artifacts/submitted.csv'
    reviewer_path = root / 'artifacts/reviewer.json'
    csv_path.write_bytes(ratings(bundle, label=label, preference=preference))
    reviewer_path.write_bytes(reviewer_bytes(reviewer_id=reviewer_id, **reviewer))
    return csv_path, reviewer_path


def retained(folder):
    return {path.name: path.read_bytes() for path in folder.iterdir()}


def test_build_freezes_raw_sources_and_replays_without_original_exports(tmp_path):
    selection, inputs, output, bundle, result = built(tmp_path)
    assert result['pairs'] == 5 and result['case_clusters'] == 4
    assert len({pair['source_job_id'] for pair in selection['pairs']}) == 4
    assert len({pair['pack']['sha256'] for pair in selection['pairs']}) == 4
    assert selection['pairs'][0]['pack'] == selection['pairs'][1]['pack']
    assert selection['pairs'][0]['source_job_id'] == selection['pairs'][1]['source_job_id']
    assert result['quality_evaluated'] is False and result['ratings_created'] is False
    assert result['independence_verified'] is False
    reviewer = output / 'reviewer'
    assert {path.name for path in reviewer.iterdir()} == {
        'review.json', 'review.html', 'ratings.template.csv', 'ratings.csv'}
    assert (reviewer / 'ratings.csv').read_bytes() == (reviewer / 'ratings.template.csv').read_bytes()
    display = (reviewer / 'review.json').read_text(encoding='utf-8')
    for private in ('C:\\\\private', 'local_receipt', 'source_job_id', 'condition_id',
                    '"variant":', 'prompt_sha256', 'recommended_check_id', 'gpt-oss'):
        assert private not in display
    for reference in (reference for pair in selection['pairs']
                      for reference in (pair['pack'], *(v['artifact'] for v in pair['variants'])) if reference):
        assert (output / 'organizer/sources' / (reference['sha256'] + '.json')).read_bytes() == inputs[reference['path']]
    initial = files.score_pair_files(tmp_path, output)
    assert initial['active_receipts'] == []
    assert initial['case_clusters']['count'] == 4
    assert initial['quality_status'] == 'unreviewed_no_nonblank_ratings'
    for name in inputs:
        (tmp_path / name).unlink()
    assert files.score_pair_files(tmp_path, output) == initial
    assert files._load(tmp_path, output)[1] == bundle


def test_file_selection_cannot_alias_one_source_as_a_second_case_cluster(tmp_path):
    selection, inputs, selection_path, output = prepare(tmp_path)
    repeated = deepcopy(selection['pairs'][0])
    repeated['unit_id'] = 'source-alias'
    original_path = repeated['pack']['path']
    alias_path = 'artifacts/synthetic/source-alias/pack.json'
    repeated['pack']['path'] = alias_path
    alias_file = tmp_path / alias_path
    alias_file.parent.mkdir(parents=True)
    alias_file.write_bytes(inputs[original_path])
    selection['pairs'].append(repeated)
    selection_path.write_bytes(encoded(selection))
    with pytest.raises(ValueError):
        files.build_pair_files(tmp_path, selection_path, output)
    assert not output.exists()


def test_orientation_and_order_are_sampled_once_then_replayed(tmp_path, monkeypatch):
    _, _, output, bundle, _ = built(tmp_path)
    def deny(*_args, **_kwargs):
        pytest.fail('Reopening a frozen review sampled a new blinding assignment')
    monkeypatch.setattr(files.secrets, 'token_hex', deny)
    monkeypatch.setattr(files.secrets, 'randbits', deny)
    monkeypatch.setattr(files.secrets, 'SystemRandom', deny)
    assert files._load(tmp_path, output)[1] == bundle
    files.score_pair_files(tmp_path, output)


def test_ratings_are_append_only_and_correction_requires_same_reviewer_leaf(tmp_path):
    _, _, output, bundle, _ = built(tmp_path)
    csv_path, reviewer = submitted(tmp_path, bundle)
    first = files.import_pair_ratings(tmp_path, output, csv_path, reviewer)
    folder = output / 'organizer/ratings'
    original = retained(folder)
    assert files.import_pair_ratings(tmp_path, output, csv_path, reviewer)['status'] == 'already_present'
    csv_path = output / 'reviewer/ratings.csv'
    csv_path.write_bytes(ratings(bundle, label='yes', preference='A'))
    with pytest.raises(ValueError):
        files.import_pair_ratings(tmp_path, output, csv_path, reviewer)
    with pytest.raises(ValueError):
        files.import_pair_ratings(tmp_path, output, csv_path, reviewer, supersedes=first['receipt_sha256'])
    assert retained(folder) == original
    corrected = files.import_pair_ratings(tmp_path, output, csv_path, reviewer,
        supersedes=first['receipt_sha256'], correction_reason='Completed previously missing synthetic ratings')
    assert corrected['status'] == 'imported'
    for name, raw in original.items():
        assert (folder / name).read_bytes() == raw
    assert len(retained(folder)) == 2
    csv_path.write_bytes(ratings(bundle, label='uncertain'))
    with pytest.raises(ValueError):
        files.import_pair_ratings(tmp_path, output, csv_path, reviewer,
            supersedes=first['receipt_sha256'], correction_reason='A forbidden correction branch')
    reviewer.write_bytes(reviewer_bytes(reviewer_id='another-reviewer'))
    with pytest.raises(ValueError):
        files.import_pair_ratings(tmp_path, output, csv_path, reviewer,
            supersedes=corrected['receipt_sha256'], correction_reason='A different reviewer cannot replace it')
    report = files.score_pair_files(tmp_path, output)
    assert len(report['active_receipts']) == 1
    assert report['active_receipts'][0]['receipt_sha256'] == corrected['receipt_sha256']
    assert report['independent_human_validation'] == 'not_established'
    assert report['causal_efficacy'] == 'not_measured'


@pytest.mark.parametrize('name', [
    'manifest.json', 'organizer/review.json', 'reviewer/review.json',
    'reviewer/review.html', 'reviewer/ratings.template.csv',
])
def test_any_changed_immutable_generated_file_is_rejected(tmp_path, name):
    _, _, output, _, _ = built(tmp_path)
    path = output / name
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(ValueError):
        files.score_pair_files(tmp_path, output)


def test_changed_frozen_raw_source_is_rejected_even_if_manifest_is_resealed(tmp_path):
    _, _, output, _, _ = built(tmp_path)
    source = next((output / 'organizer/sources').iterdir())
    source.write_bytes(source.read_bytes() + b' ')
    manifest_path = output / 'manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest['files'][source.relative_to(output).as_posix()] = {
        'sha256': raw_sha(source.read_bytes()), 'bytes': source.stat().st_size}
    manifest['sha256'] = digest({key: value for key, value in manifest.items() if key != 'sha256'})
    manifest_path.write_bytes(encoded(manifest))
    with pytest.raises(ValueError, match='hash'):
        files.score_pair_files(tmp_path, output)


def test_selected_raw_hash_failure_has_no_published_review(tmp_path):
    _, inputs, selection_path, output = prepare(tmp_path)
    path = tmp_path / next(iter(inputs))
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(ValueError, match='hash'):
        files.build_pair_files(tmp_path, selection_path, output)
    assert not output.exists()


def test_native_artifact_cannot_bind_to_a_different_selected_pack(tmp_path):
    selection, inputs, selection_path, output = prepare(tmp_path)
    pack_path = selection['pairs'][0]['pack']['path']
    pack = json.loads(inputs[pack_path])
    pack['case']['problem_statement'] += ' A changed source issue.'
    raw = encoded(pack)
    (tmp_path / pack_path).write_bytes(raw)
    for pair in selection['pairs']:
        if pair['pack']['path'] == pack_path:
            pair['pack']['sha256'] = raw_sha(raw)
    selection_path.write_bytes(encoded(selection))
    with pytest.raises(ValueError):
        files.build_pair_files(tmp_path, selection_path, output)
    assert not output.exists()


def test_output_refuses_overwrite_and_workspace_or_private_scope_escape(tmp_path):
    _, _, output, _, _ = built(tmp_path)
    manifest = (output / 'manifest.json').read_bytes()
    source = tmp_path / 'artifacts/selection.json'
    for path in (output, tmp_path / 'docs/review', tmp_path / 'data/review', tmp_path / '../review'):
        with pytest.raises(ValueError):
            files.build_pair_files(tmp_path, source, path)
    assert (output / 'manifest.json').read_bytes() == manifest


@pytest.mark.parametrize('field', ['pack', 'artifact'])
def test_source_relative_path_cannot_escape_artifacts(tmp_path, field):
    selection, _, selection_path, output = prepare(tmp_path)
    reference = (selection['pairs'][0]['pack'] if field == 'pack'
                 else selection['pairs'][0]['variants'][1]['artifact'])
    reference['path'] = 'artifacts/../private.json'
    selection_path.write_bytes(encoded(selection))
    with pytest.raises(ValueError):
        files.build_pair_files(tmp_path, selection_path, output)
    assert not output.exists()


def test_input_file_and_package_byte_caps_are_checked_before_publication(tmp_path, monkeypatch):
    _, inputs, selection_path, output = prepare(tmp_path)
    monkeypatch.setattr(files, 'MAX_FILE_BYTES', selection_path.stat().st_size - 1)
    with pytest.raises(ValueError, match='size bound'):
        files.build_pair_files(tmp_path, selection_path, output)
    monkeypatch.setattr(files, 'MAX_FILE_BYTES', 8_000_000)
    monkeypatch.setattr(files, 'MAX_TOTAL_BYTES', sum(map(len, inputs.values())))
    with pytest.raises(ValueError, match='byte bound'):
        files.build_pair_files(tmp_path, selection_path, output)
    assert not output.exists()


def test_package_cap_counts_editable_template_and_manifest(tmp_path, monkeypatch):
    _, _, output, bundle, _ = built(tmp_path)
    sources = {path.stem: path.read_bytes() for path in (output / 'organizer/sources').iterdir()}
    frozen, manifest = files._manifest(bundle, sources)
    physical = sum(map(len, frozen.values())) + len(files._json_bytes(manifest)) + len(frozen['reviewer/ratings.template.csv'])
    monkeypatch.setattr(files, 'MAX_TOTAL_BYTES', physical)
    assert files._load(tmp_path, output)[1] == bundle
    monkeypatch.setattr(files, 'MAX_TOTAL_BYTES', physical - 1)
    with pytest.raises(ValueError, match='byte bound'):
        files._load(tmp_path, output)


def test_import_count_cap_keeps_exact_reimport_idempotent(tmp_path, monkeypatch):
    _, _, output, bundle, _ = built(tmp_path)
    csv_path, reviewer = submitted(tmp_path, bundle)
    first = files.import_pair_ratings(tmp_path, output, csv_path, reviewer)
    monkeypatch.setattr(files, 'MAX_RECEIPTS', 1)
    assert files.import_pair_ratings(tmp_path, output, csv_path, reviewer)['status'] == 'already_present'
    reviewer.write_bytes(reviewer_bytes(reviewer_id='agent-two'))
    with pytest.raises(ValueError, match='bound'):
        files.import_pair_ratings(tmp_path, output, csv_path, reviewer)
    assert len(list((output / 'organizer/ratings').glob('*.json'))) == 1
    assert files.score_pair_files(tmp_path, output)['active_receipts'][0]['receipt_sha256'] == first['receipt_sha256']


def test_import_total_receipt_cap_preserves_prior_bytes_and_idempotence(tmp_path, monkeypatch):
    _, _, output, bundle, _ = built(tmp_path)
    csv_path, reviewer = submitted(tmp_path, bundle)
    files.import_pair_ratings(tmp_path, output, csv_path, reviewer)
    folder = output / 'organizer/ratings'
    prior = retained(folder)
    monkeypatch.setattr(files, 'MAX_RATING_BYTES', sum(map(len, prior.values())))
    assert files.import_pair_ratings(tmp_path, output, csv_path, reviewer)['status'] == 'already_present'
    reviewer.write_bytes(reviewer_bytes(reviewer_id='agent-two'))
    with pytest.raises(ValueError, match='bound'):
        files.import_pair_ratings(tmp_path, output, csv_path, reviewer)
    assert retained(folder) == prior


def test_concurrent_writer_cannot_publish_a_rating(tmp_path):
    _, _, output, bundle, _ = built(tmp_path)
    csv_path, reviewer = submitted(tmp_path, bundle)
    with files.shared._writer(tmp_path, output):
        with pytest.raises(ValueError, match='writer'):
            files.import_pair_ratings(tmp_path, output, csv_path, reviewer)
    assert not (output / 'organizer/ratings').exists()


def test_tampered_rating_receipt_and_unknown_receipt_file_fail_closed(tmp_path):
    _, _, output, bundle, _ = built(tmp_path)
    csv_path, reviewer = submitted(tmp_path, bundle)
    files.import_pair_ratings(tmp_path, output, csv_path, reviewer)
    folder = output / 'organizer/ratings'
    receipt = next(folder.iterdir())
    original = receipt.read_bytes()
    value = json.loads(original)
    value['reviewer']['declaration'] = 'Forged declaration'
    receipt.write_bytes(encoded(value))
    with pytest.raises(ValueError):
        files.score_pair_files(tmp_path, output)
    receipt.write_bytes(original)
    (folder / 'unexpected.json').write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='Unexpected'):
        files.score_pair_files(tmp_path, output)


def test_html_escapes_untrusted_content_and_disables_external_resources(tmp_path):
    _, _, _, bundle, _ = built(tmp_path)
    value = deepcopy(bundle['display'])
    value['pairs'][0]['context']['issue'] = '<script>alert(1)</script><img src="https://private.invalid/x">'
    value['pairs'][0]['alternatives']['A']['proposals'] = [{'text': '</pre><iframe src="https://private.invalid/">'}]
    rendered = files._html(value).decode('utf-8')
    assert '<script>' not in rendered and '<img' not in rendered and '<iframe' not in rendered
    assert '&lt;script&gt;' in rendered and '&lt;/pre&gt;&lt;iframe' in rendered
    assert 'default-src &#39;none&#39;' in rendered
    assert 'base-uri &#39;none&#39;' in rendered and 'form-action &#39;none&#39;' in rendered


def test_score_export_is_new_private_json_and_never_overwrites(tmp_path):
    _, _, output, _, _ = built(tmp_path)
    path = tmp_path / 'artifacts/scores/paired.json'
    report = files.score_pair_files(tmp_path, output, path)
    assert json.loads(path.read_bytes()) == report
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        files.score_pair_files(tmp_path, output, path)
    with pytest.raises(ValueError):
        files.score_pair_files(tmp_path, output, tmp_path / 'docs/score.json')
    assert path.read_bytes() == original


@pytest.mark.skipif(os.name != 'nt', reason='Windows junction guard')
def test_actual_windows_junction_input_and_output_are_not_traversed(tmp_path):
    _, _, selection_path, output = prepare(tmp_path)
    target = tmp_path / 'elsewhere'
    target.mkdir()
    canary = target / 'canary.json'
    canary.write_text('untouched', encoding='utf-8')
    junction = tmp_path / 'artifacts/junction'
    subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command',
        'New-Item -ItemType Junction -Path $env:ISSUEINTEL_PAIR_JUNCTION -Value $env:ISSUEINTEL_PAIR_TARGET | Out-Null'],
        cwd=tmp_path, env={**os.environ, 'ISSUEINTEL_PAIR_JUNCTION': str(junction),
                           'ISSUEINTEL_PAIR_TARGET': str(target)}, capture_output=True, check=True)
    try:
        with pytest.raises(ValueError, match='reparse'):
            files._read(tmp_path, junction / 'canary.json', artifacts=True)
        with pytest.raises(ValueError, match='reparse'):
            files.build_pair_files(tmp_path, selection_path, junction / 'review')
        assert canary.read_text(encoding='utf-8') == 'untouched'
        assert not output.exists() and not (target / 'review').exists()
    finally:
        assert junction.absolute().is_relative_to(tmp_path.absolute())
        os.rmdir(junction)
