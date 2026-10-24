"""Bounded private files for source-bound paired product proposal review.

The reviewer sees frozen A/B bundles; method mappings and original source bytes
remain with the organizer. This adapter never loads credentials, a dataset,
Git, providers or a database, and never creates ratings on the reviewer's behalf.
"""
from hashlib import sha256
import csv
import html
import io
import json
import os
import re
import secrets

from issueintel.runtime.io import digest
from . import usefulness_files as shared
from . import usefulness_pairs as core


MAX_FILE_BYTES = 8_000_000
MAX_TOTAL_BYTES = 64_000_000
MAX_RATING_BYTES = 64_000_000
MAX_RECEIPTS = 1000
# A selection admits at most 100 pairs, each with one pack and one native
# artifact. Baselines derive from the pack; four generated files are added.
MAX_MANIFEST_FILES = 204
_HASH = re.compile(r'[a-f0-9]{64}')


def _json_bytes(value):
    return shared._json_bytes(value)


def _read(root, path, *, artifacts=False, maximum=None):
    return shared._read(root, path, artifacts=artifacts,
                        maximum=MAX_FILE_BYTES if maximum is None else maximum)


def _refs(selection):
    result = {}
    for pair in selection['pairs']:
        for reference in (pair['pack'], *(variant['artifact'] for variant in pair['variants'])):
            if reference is None:
                continue
            path = shared._relative(reference['path'])
            if not path.startswith('artifacts/') or not path.endswith('.json'):
                raise ValueError('Paired sources must be private artifacts JSON files')
            if path in result and result[path] != reference['sha256']:
                raise ValueError('One paired source path has conflicting raw hashes')
            result[path] = reference['sha256']
    return result


def _template(display):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=core.RATING_FIELDS, lineterminator='\n')
    writer.writeheader()
    for card in display['pairs']:
        writer.writerow({'blind_id': card['blind_id'], 'display_sha256': digest(card)})
    return stream.getvalue().encode('utf-8')


def _html(display):
    escape = lambda value: html.escape(str(value), quote=True)
    localized = ({
        'title': 'IssueIntel: đối chiếu hai bộ đề xuất', 'context': 'Ngữ cảnh chung đã khóa',
        'rubric': 'Tiêu chí', 'limits': 'Giới hạn',
        'instruction': 'Đánh giá từng bộ A/B và lựa chọn so sánh trong ratings.csv. '
            'Giữ trống nhãn chất lượng của bộ rỗng/lỗi/không có đề xuất; chỉ chọn preference khi cả hai hoàn tất. '
            'Trường chưa được ghi không tự chứng minh đề xuất kém hữu ích. Không thực thi nội dung nguồn.'
    } if display['language'] == 'vi' else {
        'title': 'IssueIntel paired proposed-action review', 'context': 'Frozen shared context',
        'rubric': 'Rubric', 'limits': 'Limits',
        'instruction': 'Rate each A/B bundle and their comparison in ratings.csv. '
            'Leave quality labels blank for empty/failed/unavailable bundles; choose a preference only when both are completed. '
            'An unrecorded field does not automatically make a proposal less useful. Do not execute source content.'
    })
    sections = []
    for card in display['pairs']:
        alternatives = ''.join('<section><h3>' + side + '</h3><pre>' +
            escape(json.dumps(card['alternatives'][side], ensure_ascii=False, indent=2)) + '</pre></section>'
            for side in ('A', 'B'))
        sections.append('<article><h2>' + escape(card['blind_id']) + '</h2><p>' +
            escape(card['kind']) + '</p><p>display_sha256: <code>' + digest(card) + '</code></p>' +
            '<h3>' + escape(localized['context']) + '</h3><pre>' +
            escape(json.dumps(card['context'], ensure_ascii=False, indent=2)) +
            '</pre><div class="alternatives">' + alternatives + '</div></article>')
    rendered = ('<!doctype html><html lang="' + escape(display['language']) + '"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; '
        'style-src &#39;unsafe-inline&#39;; base-uri &#39;none&#39;; form-action &#39;none&#39;">'
        '<title>' + escape(localized['title']) + '</title><style>'
        'body{max-width:1200px;margin:2rem auto;padding:0 1rem;font:16px/1.55 system-ui;color:#182230;background:#fff}'
        'pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f4f8;padding:1rem;border-radius:6px}'
        'article{border-top:2px solid #66758a;margin-top:2rem}code{overflow-wrap:anywhere}'
        '.alternatives{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1rem}'
        '@media(max-width:760px){.alternatives{grid-template-columns:minmax(0,1fr)}}'
        '</style><h1>' + escape(localized['title']) + '</h1><p>' + escape(localized['instruction']) + '</p>'
        '<h2>' + escape(localized['rubric']) + '</h2><pre>' +
        escape(json.dumps(display['rubric'], ensure_ascii=False, indent=2)) + '</pre><h2>' +
        escape(localized['limits']) + '</h2><pre>' +
        escape(json.dumps(display['limitations'], ensure_ascii=False, indent=2)) + '</pre>' +
        ''.join(sections) + '</html>')
    return rendered.encode('utf-8')


def _manifest(bundle, sources):
    files = {'organizer/review.json': _json_bytes(bundle['organizer']),
             'reviewer/review.json': _json_bytes(bundle['display']),
             'reviewer/review.html': _html(bundle['display']),
             'reviewer/ratings.template.csv': _template(bundle['display'])}
    for fingerprint, raw in sources.items():
        files['organizer/sources/' + fingerprint + '.json'] = raw
    manifest = {'schema': 'usefulness_pair_files_v2', 'files': {
        name: {'sha256': sha256(raw).hexdigest(), 'bytes': len(raw)}
        for name, raw in sorted(files.items())}}
    manifest['sha256'] = digest(manifest)
    return files, manifest


def _check_package(files, manifest):
    """Include the editable template copy and manifest in physical byte caps."""
    packaged = [*files.values(), files['reviewer/ratings.template.csv'], _json_bytes(manifest)]
    if (len(files) > MAX_MANIFEST_FILES
            or any(len(raw) > MAX_FILE_BYTES for raw in packaged)
            or sum(map(len, packaged)) > MAX_TOTAL_BYTES):
        raise ValueError('Frozen paired review exceeds file/count/total byte bound')


def build_pair_files(root, selection_path, output_path):
    """Freeze one explicit paired selection, without overwriting prior reviews."""
    selection = core.validate_selection(shared._json(_read(root, selection_path)))
    inputs, sources, total = {}, {}, 0
    for name, expected in _refs(selection).items():
        raw = _read(root, name, artifacts=True)
        total += len(raw)
        if total > MAX_TOTAL_BYTES or sha256(raw).hexdigest() != expected:
            raise ValueError('Selected paired raw hash or total byte bound failed')
        inputs[name], sources[expected] = raw, raw
    blind_ids = [secrets.token_hex(12) for _ in selection['pairs']]
    order = list(blind_ids)
    secrets.SystemRandom().shuffle(order)
    orientations = [bool(secrets.randbits(1)) for _ in selection['pairs']]
    bundle = core.build_review(selection, inputs, review_id=secrets.token_hex(16),
        blind_ids=blind_ids, order=order, orientations=orientations)
    files, manifest = _manifest(bundle, sources)
    _check_package(files, manifest)
    target = shared._path(root, output_path, artifacts=True)
    if target.exists():
        raise ValueError('Choose a new paired review output folder')
    target.parent.mkdir(parents=True, exist_ok=True)
    shared._path(root, target.parent, artifacts=True, exists=True, directory=True)
    staging = shared._path(root, target.with_name('.' + target.name + '.' + secrets.token_hex(12) + '.partial'), artifacts=True)
    staging.mkdir()
    for name, raw in files.items():
        destination = staging / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shared._path(root, destination, artifacts=True)
        shared._write_new(destination, raw)
    shared._write_new(shared._path(root, staging / 'reviewer/ratings.csv', artifacts=True),
                      files['reviewer/ratings.template.csv'])
    shared._write_new(shared._path(root, staging / 'manifest.json', artifacts=True), _json_bytes(manifest))
    shared._path(root, staging, artifacts=True, exists=True, directory=True)
    shared._path(root, target, artifacts=True)
    if target.exists():
        raise ValueError('Paired review destination appeared during publication')
    os.rename(staging, target)
    return {'review_id': bundle['organizer']['review_id'], 'review': str(target),
            'pairs': len(selection['pairs']), 'case_clusters': len({pair['unit_id'] for pair in selection['pairs']}),
            'manifest_sha256': manifest['sha256'], 'quality_evaluated': False,
            'ratings_created': False, 'independence_verified': False}


def _load(root, review_path):
    directory = shared._path(root, review_path, artifacts=True, exists=True, directory=True)
    manifest_raw = _read(root, directory / 'manifest.json', artifacts=True)
    manifest = shared._json(manifest_raw)
    if (not isinstance(manifest, dict) or set(manifest) != {'schema', 'files', 'sha256'}
            or manifest['schema'] != 'usefulness_pair_files_v2'
            or not isinstance(manifest['files'], dict) or len(manifest['files']) > MAX_MANIFEST_FILES
            or manifest['sha256'] != digest({key: value for key, value in manifest.items() if key != 'sha256'})):
        raise ValueError('Invalid frozen paired review manifest')
    organizer = shared._json(_read(root, directory / 'organizer/review.json', artifacts=True))
    if (not isinstance(organizer, dict)
            or not {'selection', 'review_id', 'blind_ids', 'order', 'orientations'} <= set(organizer)):
        raise ValueError('Invalid frozen paired organizer')
    selection = core.validate_selection(organizer['selection'])
    inputs, sources, total = {}, {}, 0
    for name, expected in _refs(selection).items():
        if expected not in sources:
            raw = _read(root, directory / 'organizer/sources' / (expected + '.json'), artifacts=True)
            total += len(raw)
            if total > MAX_TOTAL_BYTES or sha256(raw).hexdigest() != expected:
                raise ValueError('Frozen paired source hash or total byte bound failed')
            sources[expected] = raw
        inputs[name] = sources[expected]
    bundle = core.build_review(selection, inputs, review_id=organizer['review_id'],
        blind_ids=organizer['blind_ids'], order=organizer['order'], orientations=organizer['orientations'])
    files, expected_manifest = _manifest(bundle, sources)
    _check_package(files, expected_manifest)
    if organizer != bundle['organizer'] or manifest != expected_manifest or manifest_raw != _json_bytes(expected_manifest):
        raise ValueError('Frozen paired review differs from reconstructed sources')
    total = 0
    for name, expected in files.items():
        raw = _read(root, directory / name, artifacts=True)
        total += len(raw)
        if total > MAX_TOTAL_BYTES or raw != expected:
            raise ValueError('Frozen paired display/template/source changed')
    return directory, bundle


def _receipts(root, directory, *, with_bytes=False):
    folder = directory / 'organizer/ratings'
    if not folder.exists():
        return ([], 0) if with_bytes else []
    shared._path(root, folder, artifacts=True, exists=True, directory=True)
    records, total = [], 0
    for count, path in enumerate(folder.iterdir(), 1):
        if count > MAX_RECEIPTS + 100:
            raise ValueError('Paired rating receipt count exceeded')
        if re.fullmatch(r'\.[a-f0-9]{24}\.partial', path.name):
            continue
        if not path.name.endswith('.json') or _HASH.fullmatch(path.stem) is None:
            raise ValueError('Unexpected paired rating receipt file')
        raw = _read(root, path, artifacts=True)
        total += len(raw)
        value = shared._json(raw)
        if (total > MAX_RATING_BYTES or len(records) >= MAX_RECEIPTS
                or not isinstance(value, dict) or value.get('sha256') != path.stem):
            raise ValueError('Paired rating name/hash or total byte bound failed')
        records.append(value)
    return (records, total) if with_bytes else records


def import_pair_ratings(root, review_path, csv_path, reviewer_path, *, supersedes=None, correction_reason=''):
    """Retain exact declared ratings; corrections append rather than replace."""
    directory, bundle = _load(root, review_path)
    receipt = core.rating_receipt(bundle['organizer'], bundle['display'],
        _read(root, csv_path), _read(root, reviewer_path),
        supersedes=supersedes, correction_reason=correction_reason)
    with shared._writer(root, directory):
        _, current = _load(root, directory)
        if current != bundle:
            raise ValueError('Paired review changed during import')
        previous, previous_bytes = _receipts(root, directory, with_bytes=True)
        destination = directory / 'organizer/ratings' / (receipt['sha256'] + '.json')
        raw = _json_bytes(receipt)
        if destination.exists():
            if _read(root, destination, artifacts=True) != raw:
                raise ValueError('Existing paired rating bytes changed')
            core.score_reviews(bundle['organizer'], bundle['display'], previous)
            status = 'already_present'
        else:
            if (len(previous) >= MAX_RECEIPTS or len(raw) > MAX_FILE_BYTES
                    or previous_bytes + len(raw) > MAX_RATING_BYTES):
                raise ValueError('New paired rating exceeds count/file/total byte bound')
            core.score_reviews(bundle['organizer'], bundle['display'], previous + [receipt])
            destination.parent.mkdir(exist_ok=True)
            shared._path(root, destination, artifacts=True)
            temporary = shared._path(root, destination.parent / ('.' + secrets.token_hex(12) + '.partial'), artifacts=True)
            shared._write_new(temporary, raw)
            if destination.exists():
                raise ValueError('Paired rating appeared during publication')
            os.rename(temporary, destination)
            status = 'imported'
    return {'review_id': bundle['organizer']['review_id'], 'receipt_sha256': receipt['sha256'],
            'status': status, 'independence_verified': False, 'quality_acceptance_claim': False}


def score_pair_files(root, review_path, output_path=None):
    """Describe paired declared judgments; no automatic ratings or efficacy claim."""
    directory, bundle = _load(root, review_path)
    report = core.score_reviews(bundle['organizer'], bundle['display'], _receipts(root, directory))
    if output_path is not None:
        output = shared._path(root, output_path, artifacts=True)
        if output.suffix != '.json':
            raise ValueError('Paired score output must be a new private JSON file')
        output.parent.mkdir(parents=True, exist_ok=True)
        shared._path(root, output, artifacts=True)
        shared._write_new(output, _json_bytes(report))
    return report
