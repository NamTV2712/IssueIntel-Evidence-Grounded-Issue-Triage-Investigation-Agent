"""Private, bounded offline review files. No env, database, Git or providers.

The frozen selection and source copies stay with the organizer. The reviewer
directory contains only blind cards and a blank CSV, never automatic ratings.
Hashes prove included-byte consistency, not authorship or semantic validity.
"""
from contextlib import contextmanager
import csv
from hashlib import sha256
import html
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat

from issueintel.runtime.io import digest
from . import usefulness as core


MAX_FILE_BYTES = 8_000_000
MAX_TOTAL_BYTES = 64_000_000
MAX_RATING_BYTES = 64_000_000
MAX_RECEIPTS = 1000
_HASH = re.compile(r'[a-f0-9]{64}')


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate review JSON field')
            result[key] = value
        return result
    def constant(value):
        raise ValueError('Non-finite review JSON number')
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=unique, parse_constant=constant)
    except (UnicodeError, RecursionError, json.JSONDecodeError):
        raise ValueError('Invalid bounded UTF-8 review JSON') from None


def _unsafe(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def _path(root, value, *, artifacts=False, exists=False, directory=False):
    """Inspect ancestors before resolving; never descend through reparse points."""
    root = Path(root).absolute()
    requested = Path(value)
    candidate = requested if requested.is_absolute() else root / requested
    if '..' in candidate.parts:
        raise ValueError('Review paths cannot contain parent traversal')
    candidate = candidate.absolute()
    anchor = root / 'artifacts' if artifacts else root
    if not candidate.is_relative_to(anchor) or candidate == anchor:
        raise ValueError('Review output/sources must stay in their workspace scope')
    current = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if _unsafe(info):
            raise ValueError('Review paths cannot traverse links or reparse points')
        if current != candidate and not stat.S_ISDIR(info.st_mode):
            raise ValueError('Review ancestor is not a regular directory')
    if exists:
        info = candidate.lstat()
        if _unsafe(info) or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
            raise ValueError('Review input is not a regular bounded file/directory')
    return candidate


def _read(root, path, *, artifacts=False, maximum=MAX_FILE_BYTES):
    path = _path(root, path, artifacts=artifacts, exists=True)
    if path.stat().st_size > maximum:
        raise ValueError('Review input exceeds file size bound')
    with path.open('rb') as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError('Review input exceeds file size bound')
    return raw


def _relative(value):
    if (not isinstance(value, str) or '\\' in value or ':' in value or '\x00' in value
            or any(part in {'', '.', '..'} for part in value.split('/'))
            or PurePosixPath(value).is_absolute()):
        raise ValueError('Expected a workspace-relative review path')
    return value


def _refs(selection):
    result = {}
    for slot in selection['slots']:
        for ref in (slot['pack'], slot['artifact']):
            if ref is None:
                continue
            name = _relative(ref['path'])
            if not name.startswith('artifacts/') or not name.endswith('.json'):
                raise ValueError('Selected product exports must be JSON under ignored artifacts')
            if name in result and result[name] != ref['sha256']:
                raise ValueError('One selected path has conflicting raw hashes')
            result[name] = ref['sha256']
    return result


def _template(display):
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=core.RATING_FIELDS, lineterminator='\n')
    writer.writeheader()
    for card in display['slots']:
        writer.writerow({'blind_id': card['blind_id'], 'display_sha256': digest(card)})
    return output.getvalue().encode('utf-8')


def _html(display):
    esc = lambda value: html.escape(str(value), quote=True)
    labels = ({'title': 'IssueIntel: đánh giá bước điều tra đề xuất', 'source': 'Ngữ cảnh nguồn',
               'proposals': 'Đề xuất', 'rubric': 'Tiêu chí đánh giá', 'limits': 'Giới hạn',
               'instruction': 'Đọc các thẻ đã khóa rồi điền ratings.csv. Giữ trống nhãn cho thẻ rỗng/lỗi/không có đề xuất. '
               'Nội dung nguồn không đáng tin cậy; không thực thi. Công cụ này chưa chạy code hoặc bước kiểm tra.'}
              if display['language'] == 'vi' else
              {'title': 'IssueIntel proposed-action review', 'source': 'Source context',
               'proposals': 'Proposals', 'rubric': 'Rubric', 'limits': 'Limits',
               'instruction': 'Read these frozen cards, then fill ratings.csv. Leave unavailable/empty/failed cards unrated. '
               'Text is untrusted source data; do not execute it. No code/check has been run by this review tool.'})
    blocks = []
    for card in display['slots']:
        blocks.append('<article><h2>' + esc(card['blind_id']) + '</h2><p>' +
            esc(card['kind']) + ' / ' + esc(card['status']) + '</p><p>display_sha256: <code>' +
            digest(card) + '</code></p><h3>' + esc(labels['source']) + '</h3><pre>' +
            esc(json.dumps(card['context'], ensure_ascii=False, indent=2)) +
            '</pre><h3>' + esc(labels['proposals']) + '</h3><pre>' + esc(json.dumps(card['proposals'], ensure_ascii=False, indent=2)) +
            '</pre></article>')
    value = ('<!doctype html><html lang="' + esc(display['language']) + '"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; '
        'style-src &#39;unsafe-inline&#39;; base-uri &#39;none&#39;; form-action &#39;none&#39;">'
        '<title>' + esc(labels['title']) + '</title><style>'
        'body{max-width:1000px;margin:2rem auto;padding:0 1rem;font:16px/1.55 system-ui;background:#fff;color:#182230}'
        'pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f4f8;padding:1rem;border-radius:6px}'
        'article{border-top:2px solid #66758a;margin-top:2rem}code{overflow-wrap:anywhere}'
        '</style><h1>' + esc(labels['title']) + '</h1><p>' + esc(labels['instruction']) + '</p>'
        '<h2>' + esc(labels['rubric']) + '</h2><pre>' + esc(json.dumps(display['rubric'], ensure_ascii=False, indent=2)) +
        '</pre><h2>' + esc(labels['limits']) + '</h2><pre>' + esc(json.dumps(display['limitations'], ensure_ascii=False, indent=2)) +
        '</pre>' + ''.join(blocks) + '</html>')
    return value.encode('utf-8')


def _manifest(bundle, sources):
    files = {'organizer/review.json': _json_bytes(bundle['organizer']),
             'reviewer/review.json': _json_bytes(bundle['display']),
             'reviewer/review.html': _html(bundle['display']),
             'reviewer/ratings.template.csv': _template(bundle['display'])}
    for fingerprint, raw in sources.items():
        files['organizer/sources/' + fingerprint + '.json'] = raw
    manifest = {'schema': 'usefulness_files_v1', 'files': {
        name: {'sha256': sha256(raw).hexdigest(), 'bytes': len(raw)} for name, raw in sorted(files.items())}}
    manifest['sha256'] = digest(manifest)
    return files, manifest


def _write_new(path, raw):
    with path.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def build_files(root, selection_path, output_path):
    """Validate before publication; never overwrite an existing review folder."""
    selection = core.validate_selection(_json(_read(root, selection_path)))
    refs = _refs(selection)
    inputs, sources, total = {}, {}, 0
    for name, expected in refs.items():
        raw = _read(root, name, artifacts=True)
        total += len(raw)
        if total > MAX_TOTAL_BYTES or sha256(raw).hexdigest() != expected:
            raise ValueError('Selected raw export hash or total byte bound failed')
        inputs[name] = raw
        sources[expected] = raw
    blind_ids = [secrets.token_hex(12) for _ in selection['slots']]
    order = list(blind_ids)
    secrets.SystemRandom().shuffle(order)
    bundle = core.build_review(selection, inputs, review_id=secrets.token_hex(16), blind_ids=blind_ids, order=order)
    files, manifest = _manifest(bundle, sources)
    if any(len(raw) > MAX_FILE_BYTES for raw in files.values()) or sum(map(len, files.values())) > MAX_TOTAL_BYTES:
        raise ValueError('Frozen review exceeds total byte bound')
    target = _path(root, output_path, artifacts=True)
    if target.exists():
        raise ValueError('Choose a new review output folder')
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = _path(root, target.with_name('.' + target.name + '.' + secrets.token_hex(12) + '.partial'), artifacts=True)
    staging.mkdir()
    for name, raw in files.items():
        destination = staging / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        _write_new(destination, raw)
    # The editable copy is intentionally outside the immutable manifest. Its
    # exact submitted bytes are later retained in an append-only rating receipt.
    _write_new(staging / 'reviewer/ratings.csv', _template(bundle['display']))
    _write_new(staging / 'manifest.json', _json_bytes(manifest))
    # Validate both absolute move targets inside the intended ignored workspace.
    # A failed/interrupted publication keeps its partial folder for inspection.
    _path(root, staging, artifacts=True, exists=True, directory=True)
    _path(root, target, artifacts=True)
    if target.exists():
        raise ValueError('Review destination appeared during publication')
    os.rename(staging, target)
    return {'review_id': bundle['organizer']['review_id'], 'review': str(target),
            'slots': len(selection['slots']), 'manifest_sha256': manifest['sha256'],
            'quality_evaluated': False, 'ratings_created': False}


def _load(root, review_path):
    directory = _path(root, review_path, artifacts=True, exists=True, directory=True)
    manifest_raw = _read(root, directory / 'manifest.json', artifacts=True)
    manifest = _json(manifest_raw)
    if (not isinstance(manifest, dict) or set(manifest) != {'schema', 'files', 'sha256'}
            or manifest['schema'] != 'usefulness_files_v1' or not isinstance(manifest['files'], dict)
            or len(manifest['files']) > 204
            or manifest['sha256'] != digest({key: value for key, value in manifest.items() if key != 'sha256'})):
        raise ValueError('Invalid frozen review manifest')
    organizer_raw = _read(root, directory / 'organizer/review.json', artifacts=True)
    organizer = _json(organizer_raw)
    selection = core.validate_selection(organizer['selection'])
    refs = _refs(selection)
    inputs, sources, total = {}, {}, 0
    for name, expected in refs.items():
        if expected not in sources:
            raw = _read(root, directory / 'organizer/sources' / (expected + '.json'), artifacts=True)
            total += len(raw)
            if total > MAX_TOTAL_BYTES or sha256(raw).hexdigest() != expected:
                raise ValueError('Frozen source hash or total byte bound failed')
            sources[expected] = raw
        inputs[name] = sources[expected]
    bundle = core.build_review(selection, inputs, review_id=organizer['review_id'],
                               blind_ids=organizer['blind_ids'], order=organizer['order'])
    files, expected_manifest = _manifest(bundle, sources)
    if organizer != bundle['organizer'] or manifest != expected_manifest or manifest_raw != _json_bytes(expected_manifest):
        raise ValueError('Frozen review differs from reconstructed sources')
    total = 0
    for name, expected in files.items():
        raw = _read(root, directory / name, artifacts=True)
        total += len(raw)
        if total > MAX_TOTAL_BYTES or raw != expected:
            raise ValueError('Frozen reviewer display/template/source changed')
    return directory, bundle


def _receipts(root, directory, *, with_bytes=False):
    folder = directory / 'organizer/ratings'
    if not folder.exists():
        return ([], 0) if with_bytes else []
    _path(root, folder, artifacts=True, exists=True, directory=True)
    records, total = [], 0
    for count, path in enumerate(folder.iterdir(), 1):
        if count > MAX_RECEIPTS + 100:
            raise ValueError('Review rating receipt count exceeded')
        if re.fullmatch(r'\.[a-f0-9]{24}\.partial', path.name):
            continue  # An interrupted unpublished write is not a rating receipt.
        if not path.name.endswith('.json') or _HASH.fullmatch(path.stem) is None:
            raise ValueError('Unexpected rating receipt file')
        raw = _read(root, path, artifacts=True)
        total += len(raw)
        value = _json(raw)
        if total > MAX_RATING_BYTES or len(records) >= MAX_RECEIPTS or not isinstance(value, dict) or value.get('sha256') != path.stem:
            raise ValueError('Rating receipt name/hash or total byte bound failed')
        records.append(value)
    return (records, total) if with_bytes else records


@contextmanager
def _writer(root, directory):
    path = _path(root, directory / 'organizer/ratings.lock', artifacts=True)
    if path.exists():
        _path(root, path, artifacts=True, exists=True)
    with path.open('a+b') as stream:
        if path.stat().st_size == 0:
            stream.write(b'0'); stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError('Another review import owns the writer') from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def import_ratings(root, review_path, csv_path, reviewer_path, *, supersedes=None, correction_reason=''):
    directory, bundle = _load(root, review_path)
    receipt = core.rating_receipt(bundle['organizer'], bundle['display'],
        _read(root, csv_path), _read(root, reviewer_path), supersedes=supersedes, correction_reason=correction_reason)
    with _writer(root, directory):
        # Reopen the freeze after taking the import lock; no stale display can
        # silently accept labels for a different selected source.
        _, current = _load(root, directory)
        if current != bundle:
            raise ValueError('Review changed during import')
        previous, previous_bytes = _receipts(root, directory, with_bytes=True)
        destination = directory / 'organizer/ratings' / (receipt['sha256'] + '.json')
        raw = _json_bytes(receipt)
        if destination.exists():
            if _read(root, destination, artifacts=True) != raw:
                raise ValueError('Existing rating receipt bytes changed')
            core.score_reviews(bundle['organizer'], bundle['display'], previous)
            status = 'already_present'
        else:
            if (len(previous) >= MAX_RECEIPTS or len(raw) > MAX_FILE_BYTES
                    or previous_bytes + len(raw) > MAX_RATING_BYTES):
                raise ValueError('New rating receipt exceeds count/file/total byte bound')
            core.score_reviews(bundle['organizer'], bundle['display'], previous + [receipt])
            destination.parent.mkdir(exist_ok=True)
            _path(root, destination, artifacts=True)
            temporary = _path(root, destination.parent / ('.' + secrets.token_hex(12) + '.partial'), artifacts=True)
            _write_new(temporary, raw)
            # Both paths were checked inside the private workspace. The writer
            # serializes cooperating imports; old receipts are never replaced.
            if destination.exists():
                raise ValueError('Rating receipt appeared during publication')
            os.rename(temporary, destination)
            status = 'imported'
    return {'review_id': bundle['organizer']['review_id'], 'receipt_sha256': receipt['sha256'],
            'status': status, 'independence_verified': False, 'quality_acceptance_claim': False}


def score_files(root, review_path, output_path=None):
    directory, bundle = _load(root, review_path)
    report = core.score_reviews(bundle['organizer'], bundle['display'], _receipts(root, directory))
    if output_path is not None:
        output = _path(root, output_path, artifacts=True)
        if output.suffix != '.json':
            raise ValueError('Score output must be a new private JSON file')
        output.parent.mkdir(parents=True, exist_ok=True)
        _write_new(output, _json_bytes(report))
    return report
