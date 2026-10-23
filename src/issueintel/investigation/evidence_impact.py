"""Captured, bounded excerpt comparisons; no Git, provider or target execution.

Positive witnesses retain exact text and line spans. Search completeness and
absence are declared local observations: the full target text inventory is not
copied into this artifact, so its hashes alone cannot prove those observations.
"""
from copy import deepcopy
import hashlib
import json
import re

from issueintel.repository.snapshot import source_lines, validate_repo_path
from issueintel.runtime.io import digest
from .comparison import compare_packs
from .pipeline import validate_prepared_evidence


SCHEMA = 'product_evidence_impact_v1'
STATUSES = ('unchanged', 'moved', 'changed', 'missing', 'unavailable', 'ambiguous')
MAX_RESULT_BYTES = 12 * 1024 * 1024
DEFAULT_LIMITS = {'max_evidence': 128, 'max_excerpt_utf8': 65536,
    'max_scan_files': 10000, 'max_scan_utf8': 25000000, 'max_scan_lines': 1000000,
    'max_match_checks': 2000000, 'max_matches': 8, 'max_witness_utf8': 2097152}
LIMITATIONS = [
    'This is a descriptive comparison of captured code excerpts, not a rerun or rebinding of hypotheses or citations.',
    'Moved means identical line text was found at another locator in the fully searched admitted target text scope; it does not prove a Git rename or semantic identity.',
    'Changed means the original path is admitted but the excerpt was not found in the reported search scope; the same-coordinate observation is not a semantic mapping.',
    'Missing means the original path and excerpt were not found in the reported admitted scope, not proof that the text is absent from skipped or unavailable files.',
    'Search completeness, match counts and absence are declared captured local observations. The full target text inventory is not included; hashes do not independently prove these observations or authenticate their author.',
    'Line spans use one-based inclusive LF lines with CRLF line terminators normalized, matching the source evidence contract. Raw file hashes retain byte differences.',
    'Code containment, public release observations and verified fixes are different facts. This comparison verifies none of them and does not execute code, tests, models or evaluation.',
]
_HASH = re.compile(r'[a-f0-9]{64}')
_OID = re.compile(r'(?:[a-f0-9]{40}|[a-f0-9]{64})')
_JOB = re.compile(r'[a-f0-9]{24}')
_LOCATOR = re.compile(r'(.+):([1-9][0-9]*)-([1-9][0-9]*)')
_QUOTE_KEYS = {'path', 'locator', 'start_line', 'end_line', 'text', 'sha256',
               'revision', 'file_sha256', 'raw_sha256', 'blob_sha'}
_FILE_KEYS = {'path', 'sha256', 'raw_sha256', 'blob_sha', 'lines', 'utf8_bytes'}
_SEARCH_KEYS = {'performed', 'complete', 'files_total', 'files_scanned',
    'utf8_bytes_total', 'utf8_bytes_scanned', 'lines_total', 'lines_scanned',
    'match_count', 'match_lower_bound', 'matches_truncated', 'limit_reason',
    'inventory_sha256', 'match_checks'}


def _sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _object(value, keys, label):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError('Invalid '+label+' fields')


def _text(value, maximum, label, *, empty=False):
    if type(value) is not str or len(value) > maximum or '\x00' in value or not empty and not value:
        raise ValueError('Invalid '+label)


def _hash(value, label, *, oid=False, empty=False):
    if empty and value == '':
        return
    if type(value) is not str or (_OID if oid else _HASH).fullmatch(value) is None:
        raise ValueError('Invalid '+label+' hash')


def _integer(value, label, *, maximum=None):
    if type(value) is not int or value < 0 or maximum is not None and value > maximum:
        raise ValueError('Invalid '+label+' count')


def _bounded_json(value):
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                         allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        raise ValueError('Invalid evidence impact JSON') from None
    if len(raw) > MAX_RESULT_BYTES:
        raise ValueError('Evidence impact exceeds the result byte bound')


def _limits(value):
    value = deepcopy(DEFAULT_LIMITS if value is None else value)
    _object(value, DEFAULT_LIMITS, 'evidence impact limits')
    for key, maximum in DEFAULT_LIMITS.items():
        if type(value[key]) is not int or not 1 <= value[key] <= maximum:
            raise ValueError('Invalid evidence impact limit')
    return value


def _descriptor(snapshot):
    value = snapshot.to_dict()
    value['files'] = [{key: item for key, item in file.to_dict().items() if key != 'text'}
                      for file in snapshot.files]
    return value


def _product(pack):
    case = pack['case']
    if (pack['snapshot']['protocol'] != 'benchmark_base' or pack['snapshot'].get('cutoff') is not None
            or any(key in case for key in ('split', 'instance_id'))
            or not case['case_id'].startswith(('local:', 'synthetic:', 'https://github.com/', 'followup:'))
            or pack.get('leakage_audit', {}).get('strict_issue_time_verified') is not False):
        raise ValueError('Evidence impact requires an owned product source outside benchmark splits')
    # Authentication of a fork's actual origin belongs to the owning service.
    compare_packs(pack, pack)
    return case


def _file(file):
    return {'path': file.path, 'sha256': file.sha256, 'raw_sha256': file.raw_sha256,
            'blob_sha': file.blob_sha, 'lines': len(source_lines(file.text)),
            'utf8_bytes': len(file.text.encode('utf-8'))}


def _quote(file, revision, start, end, text=None):
    if text is None:
        text = '\n'.join(source_lines(file.text)[start-1:end])
    return {'path': file.path, 'locator': file.path+':'+str(start)+'-'+str(end),
            'start_line': start, 'end_line': end, 'text': text, 'sha256': _sha(text),
            'revision': revision, 'file_sha256': file.sha256,
            'raw_sha256': file.raw_sha256, 'blob_sha': file.blob_sha}


def _references(pack, identifier):
    hypotheses = (pack.get('annotation') or {}).get('hypotheses', [])
    return {role+'_ids': sorted(row['id'] for row in hypotheses if identifier in row[role+'_ids'])
            for role in ('support', 'counter')}


def _search_empty(inventory, totals, *, reason=None):
    return {'performed': False, 'complete': False,
        'files_total': totals['files'], 'files_scanned': 0,
        'utf8_bytes_total': totals['bytes'], 'utf8_bytes_scanned': 0,
        'lines_total': totals['lines'], 'lines_scanned': 0,
        'match_count': None, 'match_lower_bound': 0, 'matches_truncated': False,
        'limit_reason': reason, 'inventory_sha256': inventory, 'match_checks': 0}


def _classify(before, after):
    search = after['search']
    if after['path_status'] == 'skipped':
        return 'unavailable', 'source_path_skipped'
    slot = after['original_slot']
    if slot is not None and slot['text'] == before['text']:
        return 'unchanged', 'same_locator_exact_text'
    if len(after['matches']) >= 2:
        return 'ambiguous', 'multiple_exact_target_locators'
    if not search['complete']:
        return 'unavailable', search['limit_reason'] or 'search_not_complete'
    if search['match_count'] == 1 and len(after['matches']) == 1:
        return 'moved', 'one_exact_match_in_admitted_scope'
    if search['match_count'] == 0:
        return ('changed', 'same_path_excerpt_not_found') if after['path_status'] == 'admitted' else (
            'missing', 'source_path_and_excerpt_not_found_in_admitted_scope')
    return 'unavailable', search['limit_reason'] or 'match_witness_limit'


def _prefix(pattern):
    table = [0]*len(pattern)
    matched = 0
    for index in range(1, len(pattern)):
        while matched and pattern[index] != pattern[matched]:
            matched = table[matched-1]
        if pattern[index] == pattern[matched]:
            matched += 1
        table[index] = matched
    return table


def _search(pattern, target_files, metadata, revision, limits, inventory, totals, capture):
    """KMP over exact LF lines; no quadratic diff or substring-only matches."""
    result = _search_empty(inventory, totals)
    result['performed'] = True
    prefix = _prefix(pattern)
    matches = []
    count = 0
    reason = None
    for file in target_files:
        info = metadata[file.path]
        if result['files_scanned'] >= limits['max_scan_files']:
            reason = 'file_scan_limit'; break
        if result['utf8_bytes_scanned']+info['utf8_bytes'] > limits['max_scan_utf8']:
            reason = 'byte_scan_limit'; break
        if result['lines_scanned']+info['lines'] > limits['max_scan_lines']:
            reason = 'line_scan_limit'; break
        matched = 0
        lines = source_lines(file.text)
        for index, line in enumerate(lines):
            while True:
                if result['match_checks'] >= limits['max_match_checks']:
                    reason = 'match_check_limit'; break
                result['match_checks'] += 1
                if line == pattern[matched]:
                    matched += 1
                    break
                if not matched:
                    break
                matched = prefix[matched-1]
            if reason:
                break
            if matched == len(pattern):
                count += 1
                if len(matches) < limits['max_matches']:
                    quote = _quote(file, revision, index-len(pattern)+2, index+1,
                                   '\n'.join(pattern))
                    if capture(quote):
                        matches.append(quote)
                    else:
                        result['matches_truncated'] = True
                else:
                    result['matches_truncated'] = True
                matched = prefix[matched-1]
        if reason:
            break
        result['files_scanned'] += 1
        result['utf8_bytes_scanned'] += info['utf8_bytes']
        result['lines_scanned'] += info['lines']
    result['complete'] = reason is None
    result['match_count'] = count if result['complete'] else None
    result['match_lower_bound'] = count
    result['limit_reason'] = reason
    if result['complete'] and result['matches_truncated']:
        result['limit_reason'] = 'match_witness_limit'
    return matches, result


def build_impact(pack, before, after, *, source_job_id, source_pack_sha256, limits=None):
    case = _product(pack)
    limits = _limits(limits)
    before.verify(); after.verify()
    if (before.repository != after.repository or after.protocol != 'benchmark_base'
            or after.cutoff is not None or digest(_descriptor(before)) != digest(pack['snapshot'])):
        raise ValueError('Evidence impact snapshots differ from the bound product source')
    validate_prepared_evidence(case, [row for row in pack['evidence'] if row['kind'] != 'repository_context'], before)
    code = [row for row in pack['evidence'] if row['kind'] == 'code']
    if len(code) > limits['max_evidence']:
        raise ValueError('Source code evidence exceeds the impact entry bound')
    old = {file.path: file for file in before.files}
    target_files = sorted(after.files, key=lambda file: file.path)
    new = {file.path: file for file in target_files}
    metadata = {file.path: _file(file) for file in target_files}
    inventory = digest(list(metadata.values()))
    totals = {'files': len(target_files), 'bytes': sum(row['utf8_bytes'] for row in metadata.values()),
              'lines': sum(row['lines'] for row in metadata.values())}
    skipped = {row['path']: row['reason'] for row in after.skipped if 'path' in row}
    witness_bytes = 0
    def capture(quote):
        nonlocal witness_bytes
        size = len(quote['text'].encode('utf-8'))
        if witness_bytes+size > limits['max_witness_utf8']:
            return False
        witness_bytes += size
        return True
    entries = []
    for evidence in code:
        match = _LOCATOR.fullmatch(evidence['locator'])
        path, start, end = match[1], int(match[2]), int(match[3])
        original = _quote(old[path], before.commit_sha, start, end, evidence['text'])
        path_status = 'skipped' if path in skipped else 'admitted' if path in new else 'missing'
        observed = {'path_status': path_status, 'same_path_file': metadata.get(path),
            'skipped_reason': skipped.get(path), 'original_slot': None, 'matches': [],
            'search': _search_empty(inventory, totals)}
        if path_status == 'skipped':
            observed['search']['limit_reason'] = 'source_path_skipped'
        elif len(original['text'].encode('utf-8')) > limits['max_excerpt_utf8']:
            observed['search']['limit_reason'] = 'excerpt_size_limit'
        else:
            file = new.get(path)
            if file is not None and end <= metadata[path]['lines']:
                quote = _quote(file, after.commit_sha, start, end)
                if capture(quote):
                    observed['original_slot'] = quote
                else:
                    observed['search']['limit_reason'] = 'match_witness_limit'
            slot = observed['original_slot']
            if slot is None or slot['text'] != original['text']:
                observed['matches'], observed['search'] = _search(
                    original['text'].split('\n'), target_files, metadata, after.commit_sha,
                    limits, inventory, totals, capture)
        status, reason = _classify(original, observed)
        entries.append({'evidence_id': evidence['id'], 'status': status, 'reason': reason,
                        'before': original, 'hypotheses': _references(pack, evidence['id']), 'after': observed})
    counts = {status: sum(row['status'] == status for row in entries) for status in STATUSES}
    result = {'schema': SCHEMA, 'source': {'job_id': source_job_id, 'pack_sha256': source_pack_sha256},
        'repository': case['repository'], 'case_id': case['case_id'],
        'query_sha256': _sha(case['problem_statement']),
        'before': {'commit_sha': before.commit_sha, 'snapshot_sha256': before.snapshot_sha256},
        'after': {'commit_sha': after.commit_sha, 'snapshot_sha256': after.snapshot_sha256},
        'entries': entries, 'counts': counts,
        'coverage': {'source_code_evidence_total': len(code), 'reported': len(entries),
            'source_noncode_omitted': len(pack['evidence'])-len(code),
            'after_admitted_files': len(after.files), 'after_skipped_files': len(after.skipped),
            'searches_complete': sum(row['after']['search']['complete'] for row in entries),
            'searches_limited': sum(row['after']['search']['limit_reason'] is not None for row in entries)},
        'limits': limits, 'fix_status': 'unverified', 'evaluation_performed': False, 'model_calls': 0,
        'verification_basis': 'declared_captured_local_search', 'target_text_inventory_included': False,
        'limitations': list(LIMITATIONS)}
    return validate_impact(dict(result, sha256=digest(result)), pack=pack,
        source_job_id=source_job_id, source_pack_sha256=source_pack_sha256)


def _check_quote(value, revision):
    _object(value, _QUOTE_KEYS, 'excerpt witness')
    validate_repo_path(value['path'])
    for key in ('start_line', 'end_line'):
        _integer(value[key], 'excerpt '+key, maximum=100000000)
    start, end = value['start_line'], value['end_line']
    if (start < 1 or end < start or value['revision'] != revision
            or value['locator'] != value['path']+':'+str(start)+'-'+str(end)):
        raise ValueError('Excerpt locator or revision changed')
    _text(value['text'], MAX_RESULT_BYTES, 'excerpt text', empty=True)
    if len(value['text'].split('\n')) != end-start+1 or _sha(value['text']) != value['sha256']:
        raise ValueError('Excerpt text, span or digest changed')
    for key in ('sha256', 'file_sha256', 'raw_sha256'):
        _hash(value[key], 'excerpt '+key)
    _hash(value['blob_sha'], 'excerpt blob', oid=True)


def _check_file(value):
    _object(value, _FILE_KEYS, 'target file witness')
    validate_repo_path(value['path'])
    for key in ('sha256', 'raw_sha256'):
        _hash(value[key], 'target file '+key)
    _hash(value['blob_sha'], 'target file blob', oid=True)
    for key in ('lines', 'utf8_bytes'):
        _integer(value[key], 'target file '+key, maximum=100000000)


def validate_impact(value, *, pack=None, source_job_id=None, source_pack_sha256=None):
    """Replay captured witnesses and declarations without fetching target text.

    Source-pack authority belongs to the owning caller. Search coverage is a
    recorded observation, not an independently replayed proof of absence.
    """
    _bounded_json(value)
    _object(value, {'schema','source','repository','case_id','query_sha256','before','after',
        'entries','counts','coverage','limits','fix_status','evaluation_performed','model_calls',
        'verification_basis','target_text_inventory_included','limitations','sha256'}, 'evidence impact')
    if (value['schema'] != SCHEMA or value['fix_status'] != 'unverified'
            or value['evaluation_performed'] is not False or type(value['model_calls']) is not int
            or value['model_calls'] != 0 or value['target_text_inventory_included'] is not False
            or value['verification_basis'] != 'declared_captured_local_search'
            or value['limitations'] != LIMITATIONS):
        raise ValueError('Evidence comparison cannot assert execution, evaluation or a verified fix')
    _object(value['source'], {'job_id','pack_sha256'}, 'impact source')
    if type(value['source']['job_id']) is not str or _JOB.fullmatch(value['source']['job_id']) is None:
        raise ValueError('Invalid impact source job')
    _hash(value['source']['pack_sha256'], 'impact source pack')
    if (source_job_id is not None and value['source']['job_id'] != source_job_id
            or source_pack_sha256 is not None and value['source']['pack_sha256'] != source_pack_sha256):
        raise ValueError('Evidence comparison belongs to another source')
    _text(value['repository'], 2000, 'impact repository')
    _text(value['case_id'], 2000, 'impact case')
    _hash(value['query_sha256'], 'impact query')
    for side in ('before','after'):
        _object(value[side], {'commit_sha','snapshot_sha256'}, 'impact '+side)
        _hash(value[side]['commit_sha'], side+' commit', oid=True)
        _hash(value[side]['snapshot_sha256'], side+' snapshot')
    limits = _limits(value['limits'])
    entries = value['entries']
    if type(entries) is not list or len(entries) > limits['max_evidence']:
        raise ValueError('Invalid impact entry count')
    coverage = value['coverage']
    _object(coverage, {'source_code_evidence_total','reported','source_noncode_omitted',
        'after_admitted_files','after_skipped_files','searches_complete','searches_limited'}, 'impact coverage')
    for key, number in coverage.items():
        _integer(number, 'coverage '+key, maximum=100000000)
    if coverage['reported'] != len(entries) or coverage['source_code_evidence_total'] != len(entries):
        raise ValueError('Impact source inventory changed')
    seen, target_files = set(), {}
    path_states, file_witnesses, quote_bounds = {}, {}, {}
    inventory = None
    witness_bytes = 0
    counts = {status: 0 for status in STATUSES}
    for row in entries:
        _object(row, {'evidence_id','status','reason','before','hypotheses','after'}, 'impact entry')
        _text(row['evidence_id'], 200, 'impact evidence ID')
        if row['evidence_id'] in seen:
            raise ValueError('Duplicate impact source evidence')
        seen.add(row['evidence_id'])
        _check_quote(row['before'], value['before']['commit_sha'])
        _object(row['hypotheses'], {'support_ids','counter_ids'}, 'impact hypothesis references')
        for ids in row['hypotheses'].values():
            if type(ids) is not list or len(ids) > 100:
                raise ValueError('Invalid impact hypothesis references')
            for identifier in ids:
                _text(identifier, 200, 'hypothesis reference')
            if ids != sorted(set(ids)):
                raise ValueError('Duplicate or unordered hypothesis references')
        observed = row['after']
        _object(observed, {'path_status','same_path_file','skipped_reason','original_slot',
                          'matches','search'}, 'target excerpt observation')
        if type(observed['path_status']) is not str or observed['path_status'] not in {'admitted','missing','skipped'}:
            raise ValueError('Invalid target path status')
        source_path = row['before']['path']
        if source_path in path_states and path_states[source_path] != observed['path_status']:
            raise ValueError('Target path states disagree across excerpts')
        path_states[source_path] = observed['path_status']
        if observed['path_status'] == 'admitted':
            _check_file(observed['same_path_file'])
            if observed['same_path_file']['path'] != row['before']['path'] or observed['skipped_reason'] is not None:
                raise ValueError('Target same-path file changed')
            file = observed['same_path_file']
            if source_path in file_witnesses and file_witnesses[source_path] != file:
                raise ValueError('Target file witnesses disagree across excerpts')
            file_witnesses[source_path] = file
            metadata = (file['sha256'],file['raw_sha256'],file['blob_sha'])
            if source_path in target_files and target_files[source_path] != metadata:
                raise ValueError('Target file hashes disagree across witnesses')
            target_files[source_path] = metadata
        elif observed['same_path_file'] is not None or observed['original_slot'] is not None:
            raise ValueError('An unavailable path cannot retain a same-path excerpt')
        if observed['path_status'] == 'skipped':
            _text(observed['skipped_reason'], 2000, 'skipped target reason')
        elif observed['skipped_reason'] is not None:
            raise ValueError('Invalid skipped target reason')
        matches = observed['matches']
        if type(matches) is not list or len(matches) > limits['max_matches']:
            raise ValueError('Invalid target match inventory')
        quotes = ([observed['original_slot']] if observed['original_slot'] is not None else [])+matches
        positions = []
        for quote in quotes:
            _check_quote(quote, value['after']['commit_sha'])
            witness_bytes += len(quote['text'].encode('utf-8'))
            metadata = (quote['file_sha256'],quote['raw_sha256'],quote['blob_sha'])
            if quote['path'] in target_files and target_files[quote['path']] != metadata:
                raise ValueError('Target file hashes disagree across excerpts')
            target_files[quote['path']] = metadata
            previous = quote_bounds.get(quote['path'], (0, 0))
            quote_bounds[quote['path']] = (max(previous[0], quote['end_line']),
                max(previous[1], len(quote['text'].encode('utf-8'))))
            file = observed['same_path_file']
            if file is not None and quote['path'] == file['path']:
                if (metadata != (file['sha256'],file['raw_sha256'],file['blob_sha'])
                        or quote['end_line'] > file['lines']):
                    raise ValueError('Target excerpt differs from its file witness')
        slot = observed['original_slot']
        if slot is not None and any(slot[key] != row['before'][key] for key in ('path','locator','start_line','end_line')):
            raise ValueError('The original target coordinates changed')
        for quote in matches:
            if quote['text'] != row['before']['text'] or quote['sha256'] != row['before']['sha256']:
                raise ValueError('An exact match must retain the source text')
            positions.append((quote['path'],quote['start_line'],quote['end_line']))
        if positions != sorted(set(positions)):
            raise ValueError('Target match locators duplicated or reordered')
        search = observed['search']
        _object(search, _SEARCH_KEYS, 'captured search')
        for key in ('performed','complete','matches_truncated'):
            if type(search[key]) is not bool:
                raise ValueError('Search flags require booleans')
        for key in _SEARCH_KEYS-{'performed','complete','match_count','matches_truncated','limit_reason','inventory_sha256'}:
            _integer(search[key], 'search '+key, maximum=1000000000000)
        _hash(search['inventory_sha256'], 'target search inventory')
        signature = tuple(search[key] for key in ('inventory_sha256','files_total','utf8_bytes_total','lines_total'))
        if inventory is not None and signature != inventory:
            raise ValueError('Search target inventories disagree')
        inventory = signature
        if search['files_total'] != coverage['after_admitted_files']:
            raise ValueError('Search file coverage changed')
        for unit, limit in (('files','max_scan_files'),('utf8_bytes','max_scan_utf8'),('lines','max_scan_lines')):
            if search[unit+'_scanned'] > min(search[unit+'_total'], limits[limit]):
                raise ValueError('Search coverage exceeds its bound')
        if search['match_checks'] > limits['max_match_checks']:
            raise ValueError('Search work exceeds its bound')
        reasons = {None,'source_path_skipped','excerpt_size_limit','match_witness_limit',
                   'file_scan_limit','byte_scan_limit','line_scan_limit','match_check_limit'}
        if (search['limit_reason'] is not None and type(search['limit_reason']) is not str
                or search['limit_reason'] not in reasons):
            raise ValueError('Unknown captured search limit')
        lower = search['match_lower_bound']
        if lower < len(matches) or search['matches_truncated'] != (lower > len(matches)):
            raise ValueError('Captured match counts disagree with witnesses')
        if search['complete']:
            _integer(search['match_count'], 'exact match count')
            if (not search['performed'] or search['match_count'] != lower
                    or search['limit_reason'] not in {None,'match_witness_limit'}
                    or any(search[unit+'_scanned'] != search[unit+'_total'] for unit in ('files','utf8_bytes','lines'))):
                raise ValueError('Incomplete scan cannot assert complete match coverage')
        elif search['match_count'] is not None:
            raise ValueError('Incomplete search must retain an unknown exact count')
        if not search['performed'] and (search['complete'] or lower or matches
                or any(search[key] for key in ('files_scanned','utf8_bytes_scanned','lines_scanned','match_checks'))):
            raise ValueError('A search not performed cannot assert matches or work')
        status, reason = _classify(row['before'], observed)
        if row['status'] != status or row['reason'] != reason:
            raise ValueError('Excerpt status does not follow its captured observations')
        counts[status] += 1
    for path in target_files:
        if path_states.get(path) in {'missing','skipped'}:
            raise ValueError('An unavailable path cannot contain an admitted target witness')
    minimum_lines = minimum_bytes = 0
    for path in target_files:
        lines, size = quote_bounds.get(path, (0, 0))
        if path in file_witnesses:
            file = file_witnesses[path]
            if file['lines'] < lines or file['utf8_bytes'] < size:
                raise ValueError('Target file cannot contain its captured excerpts')
            lines, size = file['lines'], file['utf8_bytes']
        minimum_lines += lines
        minimum_bytes += size
    if len(target_files) > coverage['after_admitted_files'] or (inventory is not None
            and (minimum_bytes > inventory[2] or minimum_lines > inventory[3])):
        raise ValueError('Target inventory cannot contain its captured witnesses')
    if witness_bytes > limits['max_witness_utf8']:
        raise ValueError('Target excerpt witnesses exceed their byte bound')
    _object(value['counts'], counts, 'impact counts')
    if any(type(number) is not int for number in value['counts'].values()) or value['counts'] != counts:
        raise ValueError('Impact status counts changed')
    if (coverage['searches_complete'] != sum(row['after']['search']['complete'] for row in entries)
            or coverage['searches_limited'] != sum(row['after']['search']['limit_reason'] is not None for row in entries)):
        raise ValueError('Impact search summary changed')
    if pack is not None:
        case = _product(pack)
        code = [row for row in pack['evidence'] if row['kind'] == 'code']
        if (value['repository'] != case['repository'] or value['case_id'] != case['case_id']
                or value['query_sha256'] != _sha(case['problem_statement'])
                or value['before'] != {key: pack['snapshot'][key] for key in ('commit_sha','snapshot_sha256')}
                or len(entries) != len(code) or coverage['source_noncode_omitted'] != len(pack['evidence'])-len(code)):
            raise ValueError('Impact differs from its source pack')
        files = {file['path']: file for file in pack['snapshot']['files']}
        for row, evidence in zip(entries, code):
            original = row['before']
            file = files.get(original['path'])
            if (row['evidence_id'] != evidence['id'] or original['locator'] != evidence['locator']
                    or original['text'] != evidence['text'] or original['sha256'] != _sha(evidence['text'])
                    or row['hypotheses'] != _references(pack,evidence['id']) or file is None
                    or any(original[key] != file[name] for key,name in
                           (('file_sha256','sha256'),('raw_sha256','raw_sha256'),('blob_sha','blob_sha')))):
                raise ValueError('Impact source excerpt or hypothesis references changed')
    _hash(value['sha256'], 'evidence impact')
    if value['sha256'] != digest({key:item for key,item in value.items() if key != 'sha256'}):
        raise ValueError('Evidence impact digest changed')
    return deepcopy(value)


def render_markdown(value):
    value = validate_impact(value)
    def display(text):
        text = str(text).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
        return re.sub(r'([\\`*_[\]()|])', r'\\\1', text).replace('\r',' ').replace('\n',' ')
    lines = ['# IssueIntel evidence excerpt comparison', '',
        '- Source job: '+value['source']['job_id'], '- Source pack SHA256: '+value['source']['pack_sha256'],
        '- Before commit: '+value['before']['commit_sha'], '- After commit: '+value['after']['commit_sha'],
        '- Comparison SHA256: '+value['sha256'], '- Fix status: unverified',
        '- Search basis: declared captured local observation; no independent replay of all target text.', '']
    for entry in value['entries']:
        lines += ['## '+display(entry['evidence_id'])+' — '+entry['status'], '', display(entry['reason']), '',
            'Source: '+display(entry['before']['locator'])+' · '+entry['before']['sha256'], '']
        lines += ['    '+line for line in entry['before']['text'].split('\n')]
        for quote in ([entry['after']['original_slot']] if entry['after']['original_slot'] is not None else [])+entry['after']['matches']:
            lines += ['', 'Target observation: '+display(quote['locator'])+' · '+quote['sha256'], '']
            lines += ['    '+line for line in quote['text'].split('\n')]
        lines += ['', 'Hypothesis support references: '+display(', '.join(entry['hypotheses']['support_ids']) or 'none'),
                  'Hypothesis counter references: '+display(', '.join(entry['hypotheses']['counter_ids']) or 'none'), '']
    lines += ['## Scope and limitations', '']+['- '+display(line) for line in value['limitations']]+['']
    return '\n'.join(lines)
